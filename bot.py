"""
rarbtc.com NFT Trading Automation Bot
Cycle count is determined live from the platform's reservations available today.
Credentials are loaded from environment variables — never hardcoded.
"""

import os
import sys
import time
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# SendGrid email notifications (optional — bot continues if not configured)
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
    SENDGRID_AVAILABLE = True
except ImportError:
    SENDGRID_AVAILABLE = False

# ── Telegram notifications (optional) ─────────────────────────────────────
try:
    from telegram import Bot
    from telegram.error import TelegramError
    TELEGRAM_AVAILABLE = True
except ImportError:  # pragma: no cover
    TELEGRAM_AVAILABLE = False

# ── Load environment variables ────────────────────────────────────────────────
load_dotenv()

# Multi-account support — credentials loaded per account at runtime
# ACCOUNT_COUNT is a GitHub Actions variable (not secret)
ACCOUNT_COUNT = int(os.environ.get("ACCOUNT_COUNT", "1"))

# ── Email notification config (optional) ──────────────────────────────────────
SENDGRID_API_KEY  = os.environ.get("SENDGRID_API_KEY", "")
NOTIFY_EMAIL_TO   = os.environ.get("NOTIFY_EMAIL_TO", "")
NOTIFY_EMAIL_FROM = os.environ.get("NOTIFY_EMAIL_FROM", "")
EMAIL_ENABLED     = bool(SENDGRID_API_KEY and NOTIFY_EMAIL_TO and NOTIFY_EMAIL_FROM)

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_URL         = "https://rarbtc.com"
RESERVATION_URL  = f"{BASE_URL}/nft/reservation"
MY_NFTS_URL      = f"{BASE_URL}/nft/my"
MAX_RETRIES      = 3
RETRY_DELAY_S    = 10
POPUP_TIMEOUT_MS = 180_000    # 3 minutes for order popup
SELL_WAIT_S      = 120        # 2 minutes after sell

# ── Logging setup ─────────────────────────────────────────────────────────────
logs_dir = Path("logs")
logs_dir.mkdir(exist_ok=True)

log_filename = logs_dir / f"bot_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(log_filename, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("rarbtc-bot")


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_account_credentials(account_num: int):
    """
    Load credentials for a given account number.
    Returns (username, password, reservation_password) or None if secrets missing.
    """
    username  = os.environ.get(f"RARBTC_USERNAME_{account_num}", "")
    password  = os.environ.get(f"RARBTC_PASSWORD_{account_num}", "")
    res_pass  = os.environ.get(f"RARBTC_RESERVATION_PASSWORD_{account_num}", "")
    missing   = []
    if not username:
        missing.append(f"RARBTC_USERNAME_{account_num}")
    if not password:
        missing.append(f"RARBTC_PASSWORD_{account_num}")
    if not res_pass:
        missing.append(f"RARBTC_RESERVATION_PASSWORD_{account_num}")
    if missing:
        return None, missing
    return (username, password, res_pass), []


def validate_env() -> None:
    """Warn if ACCOUNT_COUNT is not set. Individual account creds checked at runtime."""
    if ACCOUNT_COUNT < 1:
        log.error("ACCOUNT_COUNT must be >= 1. Got: %d", ACCOUNT_COUNT)
        sys.exit(1)
    log.info("ACCOUNT_COUNT = %d", ACCOUNT_COUNT)


def retry(fn, label: str, max_attempts: int = MAX_RETRIES):
    """Call fn() up to max_attempts times; raise on final failure."""
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            log.info("[%s] Attempt %d/%d …", label, attempt, max_attempts)
            result = fn()
            log.info("[%s] ✓ Success on attempt %d.", label, attempt)
            return result
        except Exception as exc:
            last_exc = exc
            log.warning("[%s] Attempt %d failed: %s", label, attempt, exc)
            if attempt < max_attempts:
                log.info("Waiting %ds before retry …", RETRY_DELAY_S)
                time.sleep(RETRY_DELAY_S)
    log.error("[%s] All %d attempts failed. Aborting run.", label, max_attempts)
    raise last_exc


# ── Core automation ───────────────────────────────────────────────────────────

class RarbtcBot:
    def __init__(self, page, username: str, password: str, reservation_password: str, account_num: int):
        self.page                 = page
        self.username             = username
        self.password             = password
        self.reservation_password = reservation_password
        self.account_num          = account_num
        self.log                  = log  # can be overridden per account

    # ── Authentication ────────────────────────────────────────────────────────

    def login(self) -> None:
        self.log.info("Navigating to login page ...")
        self.page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
        time.sleep(3)

        # Step 1: Dismiss cookie consent popup if present
        try:
            cookie_btn = self.page.query_selector("button.accept-btn")
            if cookie_btn and cookie_btn.is_visible():
                cookie_btn.click()
                self.log.info("Dismissed cookie consent popup.")
                time.sleep(1)
        except Exception:
            pass

        # Step 2: Click "Email login" tab to ensure email form is active
        try:
            email_tab = self.page.query_selector("#tab-0")
            if email_tab and email_tab.is_visible():
                email_tab.click()
                self.log.info("Clicked Email login tab.")
                time.sleep(1)
        except Exception:
            self.log.warning("Could not click Email login tab — proceeding anyway.")

        # Step 3: Fill email field (exact placeholder from live site HTML)
        self.log.info("Filling email field ...")
        self.page.fill("input[placeholder='Please enter your email']", self.username, timeout=15_000)
        self.log.info("Email filled.")

        # Step 4: Fill password field (exact placeholder from live site HTML)
        self.log.info("Filling password field ...")
        self.page.fill("input[placeholder='Password must be 8-20 characters or more']", self.password, timeout=15_000)
        self.log.info("Password filled.")

        # Step 5: Click the Login button (a <div class="bt flex-center"> on this site)
        self.log.info("Clicking Login button ...")
        self.page.click("div.bt.flex-center", timeout=10_000)

        # Wait for URL to actually change away from /login (up to 20s)
        self.log.info("Waiting for redirect away from login page ...")
        try:
            self.page.wait_for_url(
                lambda url: "/login" not in url,
                timeout=20_000
            )
        except Exception:
            pass  # Fall through to manual check below

        time.sleep(5)
        self.log.info("Post-login URL: %s", self.page.url)

        # Success = landed anywhere other than /login
        if "/login" in self.page.url:
            raise RuntimeError("Login failed — credentials rejected. Verify GitHub Secrets.")

        self.log.info("Login successful. Now on: %s", self.page.url)

        # Step 6: Close post-login promotional popup
        # Structure confirmed: div.notice-btn > div[Previous] + div[Close]
        time.sleep(5)
        for attempt in range(3):
            try:
                close_btn = self.page.query_selector("div.notice-btn div:last-child")
                if close_btn and close_btn.is_visible():
                    close_btn.click()
                    self.log.info("Closed post-login promotional popup (attempt %d).", attempt + 1)
                    time.sleep(10)
                    break
                else:
                    self.log.info("Popup not visible yet, waiting (attempt %d)...", attempt + 1)
                    time.sleep(5)
            except Exception as e:
                self.log.warning("Popup close attempt %d failed: %s", attempt + 1, e)
                time.sleep(5)

    # ── Reserve NFT ───────────────────────────────────────────────────────────

    def reserve_nft(self) -> None:
        self.log.info("Navigating to reservation page ...")
        self.page.goto(RESERVATION_URL, wait_until="domcontentloaded")
        time.sleep(10)

        # Close any promotional popup on this page
        try:
            close_btn = self.page.query_selector("div.notice-btn div:last-child")
            if close_btn and close_btn.is_visible():
                close_btn.click()
                self.log.info("Closed popup on reservation page.")
                time.sleep(10)
        except Exception:
            pass

        # Also dismiss any tutorial overlay (Skip button)
        try:
            skip_btn = self.page.query_selector("text='Skip'")
            if skip_btn and skip_btn.is_visible():
                skip_btn.click()
                self.log.info("Dismissed tutorial overlay.")
                time.sleep(5)
        except Exception:
            pass

        # Check amount available before reserving
        self.ensure_no_nfts_before_reserve()

        # Ensure NFTs cleared before reserving
        self.ensure_nfts_cleared_before_reserve()

        # Re-navigate to reservation page
        self.log.info("Navigating to reservation page before clicking ...")
        self.page.goto(RESERVATION_URL, wait_until="domcontentloaded")
        time.sleep(10)

        # Close popup if reappeared
        try:
            close_btn = self.page.query_selector("div.notice-btn div:last-child")
            if close_btn and close_btn.is_visible():
                close_btn.click()
                time.sleep(5)
        except Exception:
            pass

        self.log.info("Clicking Reservation button ...")
        # Confirmed from HTML: <button class="one-bt">Reservation</button>
        self.page.click("button.one-bt", timeout=20_000)
        time.sleep(10)

        self.log.info("Waiting for Fund password popup ...")
        # Confirmed from HTML: hidden text input with maxlength=6 inside div.pw
        self.page.wait_for_selector("div.pw input[type='text']", timeout=20_000)
        time.sleep(5)

        self.log.info("Entering fund password ...")
        # Click the visual PIN display first to focus the hidden input
        try:
            self.page.click("div.van-password-input", timeout=5_000)
            time.sleep(2)
        except Exception:
            pass
        # Type directly into the hidden input
        self.page.fill("div.pw input[type='text']", self.reservation_password, timeout=10_000)
        time.sleep(10)

        self.log.info("Clicking Confirm button ...")
        # Confirmed from HTML: button.van-button--primary > div > span.van-button__text "Confirm"
        self.page.click("button.van-button--primary", timeout=10_000)
        time.sleep(10)

        self.log.info("Waiting up to 3 min for Reservation Successful popup ...")
        # Confirmed selector: h3.popup-title "Reservation Successful"
        # with div.but containing View NFT and Sell NFT buttons
        self.page.wait_for_selector(
            "text='Reservation Successful'",
            timeout=POPUP_TIMEOUT_MS,
        )
        time.sleep(5)
        self.log.info("Reservation Successful popup appeared.")

        # Click Sell NFT — second button inside div.but
        self.log.info("Clicking Sell NFT in reservation popup ...")
        self.page.click("div.but button:last-child", timeout=10_000)
        time.sleep(10)
        self.log.info("Clicked Sell NFT in reservation success popup.")

    # ── Sell after reservation ────────────────────────────────────────────────

    def sell_from_popup(self) -> None:
        # After clicking "Sell NFT" in the Reservation Successful popup,
        # the site may redirect to /nft/reservation/list or other pages.
        # Always force navigate directly to /nft/my for consistent state.
        self.log.info("Force navigating to My NFT page for sell ...")
        self.page.goto(MY_NFTS_URL, wait_until="domcontentloaded")
        time.sleep(10)

        # Close promotional popup if present
        try:
            close_btn = self.page.query_selector("div.notice-btn div:last-child")
            if close_btn and close_btn.is_visible():
                close_btn.click()
                self.log.info("Closed popup on My NFT page.")
                time.sleep(5)
        except Exception:
            pass

        # Find Sell NFT button — confirmed: button[data-v-5055aed9]
        sell_btn = self.page.query_selector("button[data-v-5055aed9]")
        if not sell_btn:
            sell_btn = self.page.query_selector("button:has-text('Sell NFT')")

        if not sell_btn or not sell_btn.is_visible():
            self.log.warning("No Sell NFT button found — NFT may already be listed for sale.")
            return

        sell_btn.click()
        self.log.info("Clicked Sell NFT button.")
        time.sleep(10)

        # Wait for NFT Sale popup
        try:
            self.page.wait_for_selector("text='NFT Sale'", timeout=20_000)
            self.log.info("NFT Sale popup appeared.")
        except Exception:
            self.log.warning("NFT Sale popup did not appear — NFT may already be listed.")
            return

        time.sleep(5)
        self.page.click("button.van-button--primary", timeout=10_000)
        self.log.info("Clicked Sell NFT in sale popup.")
        time.sleep(10)

        # Success popup
        self.log.info("Waiting for sale confirmation ...")
        self.page.wait_for_selector(
            "text='Selling application submitted successfully'",
            timeout=20_000
        )
        self.log.info("Sale submitted successfully.")
        try:
            self.page.click("button.van-button--primary", timeout=5_000)
            self.log.info("Clicked I understand.")
        except Exception:
            self.log.info("I understand button already gone — sale confirmed.")
        time.sleep(10)

        self.log.info("Waiting 5 minutes before next cycle ...")
        time.sleep(300)
        self.log.info("5 minute wait complete.")

    def sell_from_my_nfts(self) -> None:
        self.log.info("Navigating to My NFTs page ...")
        self.page.goto(MY_NFTS_URL, wait_until="domcontentloaded")
        time.sleep(10)

        # Close promotional popup if present
        try:
            close_btn = self.page.query_selector("div.notice-btn div:last-child")
            if close_btn and close_btn.is_visible():
                close_btn.click()
                self.log.info("Closed popup on My NFTs page.")
                time.sleep(10)
        except Exception:
            pass

        # Check NFT total number — look for "NFT total number" stat
        # If 0 NFTs, nothing to sell
        try:
            page_text = self.page.inner_text("body")
            if "0 piece" in page_text and "NFT total number" in page_text:
                # Confirm it's the total number that's 0
                total_el = self.page.query_selector("div.van-list")
                if not total_el or not total_el.query_selector("li"):
                    self.log.info("No NFTs available on My NFTs page — nothing to sell.")
                    return
        except Exception:
            pass

        # Find all Sell NFT buttons — confirmed selector: button[data-v-5055aed9]
        sell_buttons = self.page.query_selector_all("button[data-v-5055aed9]")
        if not sell_buttons:
            # Fallback
            sell_buttons = self.page.query_selector_all("button:has-text('Sell NFT')")

        if not sell_buttons:
            self.log.info("No Sell NFT buttons found — nothing to sell.")
            return

        self.log.info("Found %d NFT(s) to sell.", len(sell_buttons))
        for i, btn in enumerate(sell_buttons, 1):
            self.log.info("Processing NFT %d/%d ...", i, len(sell_buttons))
            try:
                btn.click()
                time.sleep(10)

                # NFT Sale popup appears — click "Sell NFT" button inside it
                # Confirmed: span.van-button__text "Sell NFT" inside button.van-button--primary
                self.page.click("button.van-button--primary", timeout=15_000)
                self.log.info("Clicked Sell NFT in sale popup.")
                time.sleep(10)

                # Success popup: "Selling application submitted successfully"
                self.page.wait_for_selector(
                    "text='Selling application submitted successfully'",
                    timeout=20_000
                )
                self.log.info("Sale submitted successfully for NFT %d.", i)
                # Click "I understand" — popup may auto-close, so don't fail if button gone
                try:
                    self.page.click("button.van-button--primary", timeout=5_000)
                    self.log.info("Clicked I understand.")
                except Exception:
                    self.log.info("I understand button already gone — sale confirmed.")
                time.sleep(10)

            except Exception as e:
                self.log.warning("Error selling NFT %d: %s", i, e)
                continue

        self.log.info("My NFTs sell step complete.")

    # ── Full single cycle ─────────────────────────────────────────────────────

    def get_reservations_available(self) -> int:
        """Navigate to reservation page and return number of reservations available today."""
        self.log.info("Checking reservations available today ...")
        self.page.goto(RESERVATION_URL, wait_until="domcontentloaded")
        time.sleep(10)

        # Close promotional popup if present
        try:
            close_btn = self.page.query_selector("div.notice-btn div:last-child")
            if close_btn and close_btn.is_visible():
                close_btn.click()
                self.log.info("Closed popup on reservation page.")
                time.sleep(10)
        except Exception:
            pass

        # Dismiss tutorial overlay if present
        try:
            skip_btn = self.page.query_selector("text='Skip'")
            if skip_btn and skip_btn.is_visible():
                skip_btn.click()
                self.log.info("Dismissed tutorial overlay.")
                time.sleep(5)
        except Exception:
            pass

        # Find li elements containing "Number of reservations available today"
        try:
            li_els = self.page.query_selector_all("li")
            for li in li_els:
                try:
                    li_text = li.inner_text()
                    if "Number of reservations available" in li_text:
                        val_el = li.query_selector("div.val")
                        if val_el:
                            txt = val_el.inner_text().strip().replace("Times", "").strip()
                            count = int(txt)
                            self.log.info("Reservations available today: %d", count)
                            return count
                except Exception:
                    continue
        except Exception as e:
            self.log.warning("Could not read reservation count via DOM: %s", e)

        self.log.warning("Could not determine reservation count — assuming 0.")
        return 0

    def get_nft_total_count(self) -> int:
        """Read NFT total number from My NFT page using DOM check."""
        try:
            if "nft/my" not in self.page.url:
                self.page.goto(MY_NFTS_URL, wait_until="domcontentloaded")
                time.sleep(10)
                try:
                    close_btn = self.page.query_selector("div.notice-btn div:last-child")
                    if close_btn and close_btn.is_visible():
                        close_btn.click()
                        time.sleep(5)
                except Exception:
                    pass

            # Fallback: count sell buttons
            sell_btns = self.page.query_selector_all(
                "button[data-v-5055aed9], button:has-text('Sell NFT')"
            )
            count = len(sell_btns)
            self.log.info("NFT total (by sell buttons): %d", count)
            return count

        except Exception as e:
            self.log.warning("Could not read NFT total count: %s", e)
        return 0

    def ensure_nfts_cleared_before_reserve(self) -> None:
        """Before reserving, ensure NFT total is 0. Sell if needed, wait 5 min, re-check."""
        self.ensure_logged_in()
        nfts = self.get_nft_total_count()

        if nfts == 0:
            self.log.info("NFT total: 0 piece(s) — ready to reserve.")
            return

        self.log.info("%d NFT(s) found — selling before reservation ...", nfts)
        retry(self.sell_from_my_nfts, label="PreReserve:SellNFTs")
        time.sleep(10)

        self.log.info("Waiting 5 minutes for sale to reflect ...")
        time.sleep(300)

        self.ensure_logged_in()
        nfts = self.get_nft_total_count()
        if nfts == 0:
            self.log.info("NFT total now 0 — proceeding to reserve.")
            return

        self.log.info("%d NFT(s) still present — waiting another 5 minutes ...", nfts)
        time.sleep(300)
        self.ensure_logged_in()
        nfts = self.get_nft_total_count()
        if nfts == 0:
            self.log.info("NFT total now 0 — proceeding to reserve.")
        else:
            self.log.warning("%d NFT(s) still present after wait — proceeding anyway.", nfts)

    def get_today_reservation_income(self) -> str:
        """
        Navigate to /person/myIncome and read Today's personal reservation income.
        Returns the value as a string e.g. "$4.4728" or "N/A" if unreadable.
        Confirmed selector: div.info containing div.text "Today's personal reservation income"
        and sibling div.num holding the value.
        """
        try:
            self.page.goto(f"{BASE_URL}/person/myIncome", wait_until="domcontentloaded")
            time.sleep(10)
            # Close popup if present
            try:
                close_btn = self.page.query_selector("div.notice-btn div:last-child")
                if close_btn and close_btn.is_visible():
                    close_btn.click()
                    time.sleep(5)
            except Exception:
                pass
            # Find all div.info elements and look for the reservation income one
            info_els = self.page.query_selector_all("div.info")
            for el in info_els:
                try:
                    text_el = el.query_selector("div.text")
                    num_el  = el.query_selector("div.num")
                    if text_el and num_el:
                        label = text_el.inner_text().strip()
                        if "personal reservation income" in label.lower():
                            value = num_el.inner_text().strip()
                            self.log.info("Today's personal reservation income: %s", value)
                            return value
                except Exception:
                    continue
        except Exception as e:
            self.log.warning("Could not read today's reservation income: %s", e)
        return "N/A"

    def get_account_balance(self) -> float:
        """Read account balance from the reservation page."""
        try:
            if "nft/reservation" not in self.page.url:
                self.page.goto(RESERVATION_URL, wait_until="domcontentloaded")
                time.sleep(10)
                try:
                    close_btn = self.page.query_selector("div.notice-btn div:last-child")
                    if close_btn and close_btn.is_visible():
                        close_btn.click()
                        time.sleep(5)
                except Exception:
                    pass
            li_els = self.page.query_selector_all("li")
            for li in li_els:
                try:
                    li_text = li.inner_text()
                    if "Account balance" in li_text:
                        val_el = li.query_selector("div.val")
                        if val_el:
                            txt = val_el.inner_text().strip().replace("USDT", "").strip()
                            balance = float(txt)
                            self.log.info("Account balance: %.2f USDT", balance)
                            return balance
                except Exception:
                    continue
        except Exception as e:
            self.log.warning("Could not read account balance: %s", e)
        return 0.0

    def get_nfts_available(self) -> int:
        """Navigate to My NFT page and return total NFT count."""
        self.log.info("Checking NFTs available on My NFT page ...")
        self.page.goto(MY_NFTS_URL, wait_until="domcontentloaded")
        time.sleep(10)

        # Close promotional popup if present
        try:
            close_btn = self.page.query_selector("div.notice-btn div:last-child")
            if close_btn and close_btn.is_visible():
                close_btn.click()
                self.log.info("Closed popup on My NFT page.")
                time.sleep(10)
        except Exception:
            pass

        try:
            page_text = self.page.inner_text("body")
            import re as _re
            match = _re.search(r"(\d+)\s*piece", page_text)
            if match:
                count = int(match.group(1))
                self.log.info("NFTs available: %d", count)
                return count
            # Fallback: look for sell buttons
            sell_btns = self.page.query_selector_all("button[data-v-5055aed9], button:has-text('Sell NFT')")
            count = len(sell_btns)
            self.log.info("NFTs available (by sell buttons): %d", count)
            return count
        except Exception as e:
            self.log.warning("Could not read NFT count: %s", e)

        return 0


    def _get_nft_total_number(self) -> int:
        try:
            sell_btns = self.page.query_selector_all(
                "button[data-v-5055aed9], button:has-text('Sell NFT')"
            )
            count = len(sell_btns)
            self.log.info("NFT total number (sell buttons): %d", count)
            return count
        except Exception as e:
            self.log.warning("Could not read NFT total: %s", e)
            return 0

    def ensure_no_nfts_before_reserve(self) -> None:
        self.log.info("Checking NFT total before reservation ...")
        self.ensure_logged_in()
        self.page.goto(MY_NFTS_URL, wait_until="domcontentloaded")
        time.sleep(10)
        try:
            close_btn = self.page.query_selector("div.notice-btn div:last-child")
            if close_btn and close_btn.is_visible():
                close_btn.click()
                time.sleep(5)
        except Exception:
            pass
        nft_total = self._get_nft_total_number()
        if nft_total == 0:
            self.log.info("NFT total = 0 — proceeding with reservation.")
            return
        self.log.info("NFT total = %d — selling before reservation ...", nft_total)
        retry(self.sell_from_my_nfts, label="PreReserve:SellNFTs")
        self.log.info("Waiting 5 minutes for funds to reflect ...")
        time.sleep(300)
        self.ensure_logged_in()
        self.log.info("Proceeding with reservation.")

    def ensure_logged_in(self) -> None:
        """Re-login if session has expired, then close popup."""
        if "/login" in self.page.url:
            self.log.warning("Session expired — re-logging in ...")
            retry(self.login, label="ReLogin")
            # After re-login the promotional popup always appears — close it
            time.sleep(5)
            for attempt in range(3):
                try:
                    close_btn = self.page.query_selector("div.notice-btn div:last-child")
                    if close_btn and close_btn.is_visible():
                        close_btn.click()
                        self.log.info("Closed popup after re-login.")
                        time.sleep(10)
                        break
                    time.sleep(5)
                except Exception:
                    time.sleep(5)

    def run_cycle(self, cycle_num: int, total_cycles: int) -> None:
        self.log.info("=== Starting cycle %d/%d ===", cycle_num, total_cycles)

        self.ensure_logged_in()

        # Check reservations available FIRST on every cycle
        reservations = self.get_reservations_available()

        if reservations == 0:
            self.log.info("No reservations available — checking My NFT page for unsold NFTs.")
            self.ensure_logged_in()
            nfts = self.get_nfts_available()
            if nfts > 0:
                self.log.info("%d NFT(s) found — selling ...", nfts)
                retry(self.sell_from_my_nfts, label=f"Cycle{cycle_num}:SellMyNFTs")
            else:
                self.log.info("No NFTs to sell either — cycle complete.")

        elif reservations == 1:
            self.log.info("1 reservation available — selling existing NFTs first, then reserving.")
            # Step 1: Sell any existing NFTs first
            self.ensure_logged_in()
            nfts = self.get_nfts_available()
            if nfts > 0:
                self.log.info("%d existing NFT(s) found — selling before reservation.", nfts)
                retry(self.sell_from_my_nfts, label=f"Cycle{cycle_num}:SellExisting")
                time.sleep(10)
            # Step 2: Do the 1 reservation and sell
            self.ensure_logged_in()
            retry(self.reserve_nft,     label=f"Cycle{cycle_num}:Reserve")
            self.ensure_logged_in()
            retry(self.sell_from_popup, label=f"Cycle{cycle_num}:SellPopup")

        else:
            self.log.info("Reservations available: %d — proceeding with reserve + sell.", reservations)
            # Step 1: Sell any existing NFTs first
            self.ensure_logged_in()
            nfts = self.get_nfts_available()
            if nfts > 0:
                self.log.info("%d existing NFT(s) found — selling before reservation.", nfts)
                retry(self.sell_from_my_nfts, label=f"Cycle{cycle_num}:SellExisting")
                time.sleep(10)
            # Step 2: Reserve and sell
            self.ensure_logged_in()
            retry(self.reserve_nft,     label=f"Cycle{cycle_num}:Reserve")
            self.ensure_logged_in()
            retry(self.sell_from_popup, label=f"Cycle{cycle_num}:SellPopup")

        self.log.info("=== Cycle %d/%d complete. ===", cycle_num, total_cycles)


# ── Entry point ───────────────────────────────────────────────────────────────

def send_run_notification(all_account_summaries: list) -> None:
    """
    Send a single email after all accounts have been processed.
    Silently skips if email is not configured.
    """
    if not EMAIL_ENABLED:
        log.info("Email notifications not configured — skipping. "
                 "Set SENDGRID_API_KEY, NOTIFY_EMAIL_TO, NOTIFY_EMAIL_FROM to enable.")
        return

    if not SENDGRID_AVAILABLE:
        log.warning("SendGrid library not installed — cannot send notification.")
        return

    run_date = datetime.utcnow().strftime("%Y-%m-%d")
    run_time = datetime.utcnow().strftime("%H:%M UTC")

    # Build HTML email body
    account_rows = ""
    for s in all_account_summaries:
        status_color = "#27ae60" if s["status"] == "SUCCESS" else "#e74c3c"
        status_label = s["status"]

        day_income = s.get("day_income", "N/A")
        income_color = "#27ae60" if day_income != "N/A" and not day_income.startswith("-") else "#e74c3c"

        failure_row = ""
        if s.get("failure_reason"):
            failure_row = f"""
            <tr>
                <td colspan="2" style="padding:8px 12px; background:#fff3cd; color:#856404; border-radius:4px;">
                    <strong>Issue:</strong> {s["failure_reason"]}
                </td>
            </tr>"""

        account_rows += f"""
        <div style="margin-bottom:24px; border:1px solid #e0e0e0; border-radius:8px; overflow:hidden;">
            <div style="background:#2c3e50; color:white; padding:12px 16px; display:flex; justify-content:space-between;">
                <strong>Account {s["account_num"]}</strong>
                <span style="background:{status_color}; padding:2px 10px; border-radius:12px; font-size:13px;">{status_label}</span>
            </div>
            <table style="width:100%; border-collapse:collapse; font-size:14px;">
                <tr style="background:#f8f9fa;">
                    <td style="padding:8px 12px; color:#666; width:60%;">Reservations at login</td>
                    <td style="padding:8px 12px; font-weight:bold;">{s.get("reservations_start", "N/A")}</td>
                </tr>
                <tr>
                    <td style="padding:8px 12px; color:#666;">NFTs available at login</td>
                    <td style="padding:8px 12px; font-weight:bold;">{s.get("nfts_start", "N/A")}</td>
                </tr>
                <tr style="background:#f8f9fa;">
                    <td style="padding:8px 12px; color:#666;">Reservations remaining after run</td>
                    <td style="padding:8px 12px; font-weight:bold;">{s.get("reservations_end", "N/A")}</td>
                </tr>
                <tr>
                    <td style="padding:8px 12px; color:#666;">NFTs unsold after run</td>
                    <td style="padding:8px 12px; font-weight:bold;">{s.get("nfts_end", "N/A")}</td>
                </tr>
                <tr style="background:#f8f9fa;">
                    <td style="padding:8px 12px; color:#666;"><strong>Day income</strong></td>
                    <td style="padding:8px 12px; font-weight:bold; color:{income_color};">{day_income}</td>
                </tr>
                {failure_row}
            </table>
        </div>"""

    # No total across accounts — each account's income is independent

    html_body = f"""
    <div style="font-family:Arial,sans-serif; max-width:600px; margin:0 auto; color:#333;">
        <div style="background:#2c3e50; color:white; padding:20px; border-radius:8px 8px 0 0; text-align:center;">
            <h2 style="margin:0;">Rarzz NFT Bot — Daily Report</h2>
            <p style="margin:6px 0 0; opacity:0.8;">{run_date} &nbsp;|&nbsp; {run_time}</p>
        </div>

        <div style="padding:20px; background:#fff; border:1px solid #e0e0e0; border-top:none;">
            {account_rows}


        </div>

        <div style="padding:12px; text-align:center; color:#999; font-size:12px;">
            Sent by Rarzz NFT Bot &nbsp;|&nbsp; Logs available in GitHub Actions artifacts
        </div>
    </div>
    """

    try:
        message = Mail(
            from_email=NOTIFY_EMAIL_FROM,
            to_emails=NOTIFY_EMAIL_TO,
            subject=f"Rarzz NFT Bot — Daily Report {run_date}",
            html_content=html_body,
        )
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        log.info("Email notification sent. Status: %d", response.status_code)
    except Exception as e:
        log.warning("Failed to send email notification: %s", e)


def send_telegram_notification(all_summaries: list) -> None:
    """
    Send a single Telegram message after all accounts have been processed.
    Mirrors the content of the email report but formatted for Telegram.
    Silently skips if Telegram is not configured.
    """
    if not TELEGRAM_AVAILABLE:
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat_id):
        log.info("Telegram notifications not configured — skipping. "
                 "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable.")
        return

    run_date = datetime.utcnow().strftime("%Y-%m-%d")
    run_time = datetime.utcnow().strftime("%H:%M UTC")

    # Build the message text (Telegram supports MarkdownV2; we keep it simple plain text)
    lines = [
        f"*Rarzz NFT Bot — Daily Report*",
        f"{run_date} | {run_time}",
        ""  # blank line
    ]

    for s in all_summaries:
        status_emoji = "✅" if s["status"] == "SUCCESS" else ("❌" if s["status"] == "FAILED" else "⚠️")
        lines.append(f"*Account {s['account_num']}* {status_emoji} `{s['status']}`")
        lines.append(f"  Reservations at login:      {s.get('reservations_start', 'N/A')}")
        lines.append(f"  NFTs available at login:    {s.get('nfts_start', 'N/A')}")
        lines.append(f"  Reservations after run:     {s.get('reservations_end', 'N/A')}")
        lines.append(f"  NFTs unsold after run:      {s.get('nfts_end', 'N/A')}")
        income = s.get("day_income", "N/A")
        # colour is not needed in plain text; just show the value
        lines.append(f"  Day income:                 {income}")
        if s.get("failure_reason"):
            lines.append(f"  ⚠️ Issue: {s['failure_reason']}")
        lines.append("")  # separator between accounts

    # Optional total across accounts (you can compute if you wish)
    # total_income = sum(float(s.get("day_income", "0") or 0) for s in all_summaries if s.get("day_income", "N/A") != "N/A")
    # lines.append(f"*Total Day Income:* {total_income:.4f} USDT")

    message = "\n".join(lines)

    try:
        bot = Bot(token=token)
        bot.send_message(chat_id=chat_id, text=message, parse_mode="MarkdownV2")
        log.info("Telegram notification sent to chat %s", chat_id)
    except TelegramError as exc:
        log.warning("Failed to send Telegram notification: %s", exc)
    except Exception as exc:  # pragma: no cover
        log.warning("Unexpected error while sending Telegram notification: %s", exc)


def run_account(account_num: int) -> dict:
    """
    Run the full bot flow for a single account.
    Returns a summary dict with stats and status for email notification.
    """
    summary = {
        "account_num":        account_num,
        "status":             "SKIPPED",
        "failure_reason":     None,
        "reservations_start": "N/A",
        "nfts_start":         "N/A",
        "reservations_end":   "N/A",
        "nfts_end":           "N/A",
        "day_income":         "N/A",
    }

    # Load credentials for this account
    creds, missing = get_account_credentials(account_num)
    if creds is None:
        acct_log = logging.getLogger(f"rarbtc-bot-acct{account_num}")
        reason = f"Missing GitHub Secrets: {', '.join(missing)}"
        acct_log.warning("Account %d SKIPPED — %s", account_num, reason)
        summary["failure_reason"] = reason
        skip_log = logs_dir / f"account_{account_num}_SKIPPED_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.log"
        skip_log.write_text(
            f"Account {account_num} skipped at {datetime.utcnow()} UTC\n"
            f"Reason: {reason}\n",
            encoding="utf-8"
        )
        return summary

    username, password, reservation_password = creds

    # Account-specific log file
    acct_log_filename = logs_dir / f"account_{account_num}_bot_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.log"
    acct_handler = logging.FileHandler(acct_log_filename, encoding="utf-8")
    acct_handler.setFormatter(logging.Formatter(
        "%(asctime)s UTC | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    acct_logger = logging.getLogger(f"rarbtc-bot-acct{account_num}")
    acct_logger.setLevel(logging.INFO)
    acct_logger.addHandler(acct_handler)
    acct_logger.addHandler(logging.StreamHandler(sys.stdout))

    acct_logger.info("=" * 50)
    acct_logger.info("  ACCOUNT %d — Run started", account_num)
    acct_logger.info("  Log: %s", acct_log_filename)
    acct_logger.info("=" * 50)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()
        bot  = RarbtcBot(page, username, password, reservation_password, account_num)
        # Point bot logging to account logger
        bot.log = acct_logger

        try:
            retry(bot.login, label="Login")

            # ── Collect opening stats + determine cycle count ──────────────
            cycles_to_run = 0
            try:
                cycles_to_run = bot.get_reservations_available()
                summary["reservations_start"] = cycles_to_run
                bot.page.goto(MY_NFTS_URL, wait_until="domcontentloaded")
                time.sleep(10)
                summary["nfts_start"] = bot._get_nft_total_number()
            except Exception as e:
                acct_logger.warning("Could not collect opening stats: %s", e)

            if cycles_to_run == 0:
                acct_logger.info("No reservations available today — skipping cycles.")
            else:
                acct_logger.info("Reservations available: %d — running %d cycle(s).", cycles_to_run, cycles_to_run)
                for cycle in range(1, cycles_to_run + 1):
                    bot.run_cycle(cycle, cycles_to_run)

            # ── Collect closing stats ──────────────────────────────────────
            try:
                # Check reservations remaining
                summary["reservations_end"] = bot.get_reservations_available()

                # Check NFTs remaining on /nft/my
                bot.page.goto(MY_NFTS_URL, wait_until="domcontentloaded")
                time.sleep(10)
                try:
                    close_btn = bot.page.query_selector("div.notice-btn div:last-child")
                    if close_btn and close_btn.is_visible():
                        close_btn.click()
                        time.sleep(5)
                except Exception:
                    pass

                nfts_remaining = bot._get_nft_total_number()
                summary["nfts_end"] = nfts_remaining

                if nfts_remaining > 0:
                    acct_logger.warning(
                        "%d NFT(s) still listed — waiting 2 min for sales to settle ...",
                        nfts_remaining
                    )
                    time.sleep(120)
                    bot.page.goto(MY_NFTS_URL, wait_until="domcontentloaded")
                    time.sleep(10)
                    summary["nfts_end"] = bot._get_nft_total_number()
                else:
                    acct_logger.info("0 NFTs listed — waiting 2 min for funds to settle ...")
                    time.sleep(120)

                # Read today's reservation income from /person/myIncome
                summary["day_income"] = bot.get_today_reservation_income()
                acct_logger.info("Today's reservation income: %s", summary["day_income"])

            except Exception as e:
                acct_logger.warning("Could not collect closing stats: %s", e)

            summary["status"] = "SUCCESS"
            acct_logger.info("Account %d — All %d cycle(s) completed successfully.", account_num, cycles_to_run)
            return summary

        except Exception as exc:
            # Human-friendly failure reason
            err_str = str(exc)
            if "Timeout" in err_str and "login" in err_str.lower():
                reason = "Could not log in — check credentials in GitHub Secrets."
            elif "Timeout" in err_str and "Reservation Successful" in err_str:
                reason = "Reservation was placed but no confirmation appeared within 3 minutes."
            elif "Timeout" in err_str and "NFT Sale" in err_str:
                reason = "NFT was reserved but the sale popup did not appear."
            elif "Timeout" in err_str and "one-bt" in err_str:
                reason = "Reservation button not found — reservations may already be used up today."
            elif "credentials rejected" in err_str:
                reason = "Login failed — username or password is incorrect."
            elif "Missing GitHub Secrets" in err_str:
                reason = err_str
            else:
                reason = f"Unexpected error: {err_str[:200]}"

            summary["status"]         = "FAILED"
            summary["failure_reason"] = reason

            # Try to collect whatever stats we have at point of failure
            try:
                if summary["nfts_end"] == "N/A":
                    bot.page.goto(MY_NFTS_URL, wait_until="domcontentloaded")
                    time.sleep(5)
                    summary["nfts_end"] = bot._get_nft_total_number()
                if summary["day_income"] == "N/A":
                    summary["day_income"] = bot.get_today_reservation_income()
            except Exception:
                pass

            acct_logger.error("Account %d — FATAL ERROR: %s", account_num, exc, exc_info=True)
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            try:
                acct_logger.info("URL at crash: %s", page.url)
            except Exception:
                pass
            try:
                screenshot_path = logs_dir / f"account_{account_num}_error_{ts}.png"
                page.screenshot(path=str(screenshot_path), full_page=True)
                acct_logger.info("Screenshot: %s", screenshot_path)
            except Exception:
                pass
            try:
                html_path = logs_dir / f"account_{account_num}_error_page_{ts}.html"
                html_path.write_text(page.content(), encoding="utf-8")
                acct_logger.info("Page HTML: %s", html_path)
            except Exception:
                pass
            return summary

        finally:
            context.close()
            browser.close()
            acct_logger.info(
                "Account %d — Browser closed at %s UTC.",
                account_num,
                datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            )

            # Write separate human-readable daily report log for this account
            try:
                report_path = logs_dir / f"account_{account_num}_report_{datetime.utcnow().strftime('%Y%m%d')}.log"
                lines = [
                    "=" * 50,
                    f"  ACCOUNT {account_num} — Daily Report",
                    f"  Date: {datetime.utcnow().strftime('%Y-%m-%d')}",
                    f"  Status: {summary.get('status', 'UNKNOWN')}",
                    "=" * 50,
                    f"  Reservations at login:          {summary.get('reservations_start', 'N/A')}",
                    f"  NFTs available at login:        {summary.get('nfts_start', 'N/A')}",
                    f"  Reservations after run:         {summary.get('reservations_end', 'N/A')}",
                    f"  NFTs unsold after run:          {summary.get('nfts_end', 'N/A')}",
                    f"  Day income:                     {summary.get('day_income', 'N/A')}",
                ]
                if summary.get("failure_reason"):
                    lines.append("")
                    lines.append(f"  Issue: {summary['failure_reason']}")
                lines.append("=" * 50)
                report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                acct_logger.info("Daily report saved: %s", report_path)
            except Exception as re:
                acct_logger.warning("Could not write daily report: %s", re)

            acct_handler.close()
            acct_logger.removeHandler(acct_handler)


def main() -> None:
    validate_env()
    log.info("╔══════════════════════════════════════╗")
    log.info("║   rarbtc.com NFT Bot  —  Run started ║")
    log.info("╚══════════════════════════════════════╝")
    log.info("Running %d account(s) sequentially.", ACCOUNT_COUNT)

    if not EMAIL_ENABLED:
        log.info("Email notifications disabled — SENDGRID_API_KEY / NOTIFY_EMAIL_TO / "
                 "NOTIFY_EMAIL_FROM not set. Add these as GitHub Secrets to enable.")

    all_summaries = []
    for account_num in range(1, ACCOUNT_COUNT + 1):
        log.info("-" * 50)
        log.info("Starting Account %d of %d ...", account_num, ACCOUNT_COUNT)
        summary = run_account(account_num)
        all_summaries.append(summary)
        log.info("Account %d result: %s", account_num, summary["status"])

    log.info("-" * 50)
    log.info("All accounts processed. Summary:")
    for s in all_summaries:
        log.info(
            "  Account %d: %s | Day income: %s",
            s["account_num"], s["status"], s.get("day_income", "N/A")
        )

    # Send email notification after all accounts are done
    send_run_notification(all_summaries)
    # Send Telegram notification after all accounts are done
    send_telegram_notification(all_summaries)
    log.info("Run finished at %s UTC.", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))



if __name__ == "__main__":
    main()