"""
rarbtc.com NFT Trading Automation Bot
Performs up to 2 buy-sell cycles per day, fully headless via Playwright.
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

# ── Load environment variables ────────────────────────────────────────────────
load_dotenv()

# Multi-account support — credentials loaded per account at runtime
# ACCOUNT_COUNT is a GitHub Actions variable (not secret)
ACCOUNT_COUNT = int(os.environ.get("ACCOUNT_COUNT", "1"))

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_URL         = "https://rarbtc.com"
RESERVATION_URL  = f"{BASE_URL}/nft/reservation"
MY_NFTS_URL      = f"{BASE_URL}/nft/my"
MAX_CYCLES       = 2          # platform allows 2 buys/sells per 24 h
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

    def run_cycle(self, cycle_num: int) -> None:
        self.log.info("=== Starting cycle %d/%d ===", cycle_num, MAX_CYCLES)

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

        self.log.info("=== Cycle %d complete. ===", cycle_num)


# ── Entry point ───────────────────────────────────────────────────────────────

def run_account(account_num: int) -> bool:
    """
    Run the full bot flow for a single account.
    Returns True on success, False on failure.
    Sets up its own log file, browser context, and credentials.
    """
    # Load credentials for this account
    creds, missing = get_account_credentials(account_num)
    if creds is None:
        acct_log = logging.getLogger(f"rarbtc-bot-acct{account_num}")
        acct_log.warning(
            "Account %d SKIPPED — missing secrets: %s",
            account_num, ", ".join(missing)
        )
        # Write skip record to account-specific log file
        skip_log = logs_dir / f"account_{account_num}_SKIPPED_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.log"
        skip_log.write_text(
            f"Account {account_num} skipped at {datetime.utcnow()} UTC\n"
            f"Reason: Missing GitHub Secrets: {', '.join(missing)}\n",
            encoding="utf-8"
        )
        return False

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

            for cycle in range(1, MAX_CYCLES + 1):
                bot.run_cycle(cycle)

            acct_logger.info("Account %d — All %d cycles completed successfully.", account_num, MAX_CYCLES)
            return True

        except Exception as exc:
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
            return False

        finally:
            context.close()
            browser.close()
            acct_logger.info(
                "Account %d — Browser closed at %s UTC.",
                account_num,
                datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            )
            acct_handler.close()
            acct_logger.removeHandler(acct_handler)


def main() -> None:
    validate_env()
    log.info("╔══════════════════════════════════════╗")
    log.info("║   rarbtc.com NFT Bot  —  Run started ║")
    log.info("╚══════════════════════════════════════╝")
    log.info("Running %d account(s) sequentially.", ACCOUNT_COUNT)

    results = {}
    for account_num in range(1, ACCOUNT_COUNT + 1):
        log.info("─" * 50)
        log.info("Starting Account %d of %d ...", account_num, ACCOUNT_COUNT)
        success = run_account(account_num)
        results[account_num] = "SUCCESS" if success else "FAILED/SKIPPED"
        log.info("Account %d result: %s", account_num, results[account_num])

    log.info("─" * 50)
    log.info("All accounts processed. Summary:")
    for acct, result in results.items():
        log.info("  Account %d: %s", acct, result)
    log.info("Run finished at %s UTC.", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))



if __name__ == "__main__":
    main()