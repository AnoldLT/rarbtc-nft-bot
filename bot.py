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

# Credentials loaded per-account at runtime via get_account_credentials(N)
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
MAX_CYCLES_CAP   = 10         # safety cap — prevents runaway loops
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
    def __init__(self, page):
        self.page = page

    # ── Authentication ────────────────────────────────────────────────────────

    def login(self) -> None:
        log.info("Navigating to login page ...")
        self.page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
        time.sleep(3)

        # Step 1: Dismiss cookie consent popup if present
        try:
            cookie_btn = self.page.query_selector("button.accept-btn")
            if cookie_btn and cookie_btn.is_visible():
                cookie_btn.click()
                log.info("Dismissed cookie consent popup.")
                time.sleep(1)
        except Exception:
            pass

        # Step 2: Click "Email login" tab to ensure email form is active
        try:
            email_tab = self.page.query_selector("#tab-0")
            if email_tab and email_tab.is_visible():
                email_tab.click()
                log.info("Clicked Email login tab.")
                time.sleep(1)
        except Exception:
            log.warning("Could not click Email login tab — proceeding anyway.")

        # Step 3: Fill email field (exact placeholder from live site HTML)
        log.info("Filling email field ...")
        self.page.fill("input[placeholder='Please enter your email']", USERNAME, timeout=15_000)
        log.info("Email filled.")

        # Step 4: Fill password field (exact placeholder from live site HTML)
        log.info("Filling password field ...")
        self.page.fill("input[placeholder='Password must be 8-20 characters or more']", PASSWORD, timeout=15_000)
        log.info("Password filled.")

        # Step 5: Click the Login button (a <div class="bt flex-center"> on this site)
        log.info("Clicking Login button ...")
        self.page.click("div.bt.flex-center", timeout=10_000)

        # Wait for URL to actually change away from /login (up to 20s)
        log.info("Waiting for redirect away from login page ...")
        try:
            self.page.wait_for_url(
                lambda url: "/login" not in url,
                timeout=20_000
            )
        except Exception:
            pass  # Fall through to manual check below

        time.sleep(5)
        log.info("Post-login URL: %s", self.page.url)

        # Success = landed anywhere other than /login
        if "/login" in self.page.url:
            raise RuntimeError("Login failed — credentials rejected. Verify GitHub Secrets.")

        log.info("Login successful. Now on: %s", self.page.url)

        # Step 6: Close post-login promotional popup
        # Structure confirmed: div.notice-btn > div[Previous] + div[Close]
        time.sleep(5)
        for attempt in range(3):
            try:
                close_btn = self.page.query_selector("div.notice-btn div:last-child")
                if close_btn and close_btn.is_visible():
                    close_btn.click()
                    log.info("Closed post-login promotional popup (attempt %d).", attempt + 1)
                    time.sleep(10)
                    break
                else:
                    log.info("Popup not visible yet, waiting (attempt %d)...", attempt + 1)
                    time.sleep(5)
            except Exception as e:
                log.warning("Popup close attempt %d failed: %s", attempt + 1, e)
                time.sleep(5)

    # ── Reserve NFT ───────────────────────────────────────────────────────────

    def reserve_nft(self) -> None:
        log.info("Navigating to reservation page ...")
        self.page.goto(RESERVATION_URL, wait_until="domcontentloaded")
        time.sleep(10)

        # Close any promotional popup on this page
        try:
            close_btn = self.page.query_selector("div.notice-btn div:last-child")
            if close_btn and close_btn.is_visible():
                close_btn.click()
                log.info("Closed popup on reservation page.")
                time.sleep(10)
        except Exception:
            pass

        # Also dismiss any tutorial overlay (Skip button)
        try:
            skip_btn = self.page.query_selector("text='Skip'")
            if skip_btn and skip_btn.is_visible():
                skip_btn.click()
                log.info("Dismissed tutorial overlay.")
                time.sleep(5)
        except Exception:
            pass

        # Check amount available before reserving
        self.ensure_no_nfts_before_reserve()

        # Ensure NFTs cleared before reserving
        self.ensure_nfts_cleared_before_reserve()

        # Re-navigate to reservation page
        log.info("Navigating to reservation page before clicking ...")
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

        log.info("Clicking Reservation button ...")
        # Confirmed from HTML: <button class="one-bt">Reservation</button>
        self.page.click("button.one-bt", timeout=20_000)
        time.sleep(10)

        log.info("Waiting for Fund password popup ...")
        # Confirmed from HTML: hidden text input with maxlength=6 inside div.pw
        self.page.wait_for_selector("div.pw input[type='text']", timeout=20_000)
        time.sleep(5)

        log.info("Entering fund password ...")
        # Click the visual PIN display first to focus the hidden input
        try:
            self.page.click("div.van-password-input", timeout=5_000)
            time.sleep(2)
        except Exception:
            pass
        # Type directly into the hidden input
        self.page.fill("div.pw input[type='text']", RESERVATION_PASSWORD, timeout=10_000)
        time.sleep(10)

        log.info("Clicking Confirm button ...")
        # Confirmed from HTML: button.van-button--primary > div > span.van-button__text "Confirm"
        self.page.click("button.van-button--primary", timeout=10_000)
        time.sleep(10)

        log.info("Waiting up to 3 min for Reservation Successful popup ...")
        # Confirmed selector: h3.popup-title "Reservation Successful"
        # with div.but containing View NFT and Sell NFT buttons
        self.page.wait_for_selector(
            "text='Reservation Successful'",
            timeout=POPUP_TIMEOUT_MS,
        )
        time.sleep(5)
        log.info("Reservation Successful popup appeared.")

        # Click Sell NFT — second button inside div.but
        log.info("Clicking Sell NFT in reservation popup ...")
        self.page.click("div.but button:last-child", timeout=10_000)
        time.sleep(10)
        log.info("Clicked Sell NFT in reservation success popup.")

    # ── Sell after reservation ────────────────────────────────────────────────

    def sell_from_popup(self) -> None:
        # After clicking "Sell NFT" in the Reservation Successful popup,
        # the site navigates to /nft/my where the NFT Sale popup appears
        log.info("Navigating to My NFT page after reservation sell click ...")
        self.page.goto(MY_NFTS_URL, wait_until="domcontentloaded")
        time.sleep(10)

        # Close promotional popup if present
        try:
            close_btn = self.page.query_selector("div.notice-btn div:last-child")
            if close_btn and close_btn.is_visible():
                close_btn.click()
                log.info("Closed popup on My NFT page.")
                time.sleep(10)
        except Exception:
            pass

        # Check if NFT Sale popup appeared automatically after navigation
        nft_sale_visible = False
        try:
            self.page.wait_for_selector("text='NFT Sale'", timeout=10_000)
            nft_sale_visible = True
            log.info("NFT Sale popup appeared automatically.")
        except Exception:
            log.info("NFT Sale popup not auto-shown — clicking Sell NFT button manually.")

        if not nft_sale_visible:
            # Find and click Sell NFT button on the page
            sell_btn = self.page.query_selector("button[data-v-5055aed9]")
            if not sell_btn:
                sell_btn = self.page.query_selector("button:has-text('Sell NFT')")
            if sell_btn and sell_btn.is_visible():
                sell_btn.click()
                log.info("Clicked Sell NFT button.")
                time.sleep(10)
                # Wait for NFT Sale popup
                self.page.wait_for_selector("text='NFT Sale'", timeout=15_000)
                log.info("NFT Sale popup appeared.")
            else:
                log.warning("No Sell NFT button found — NFT may already be listed.")
                return

        time.sleep(5)
        # Click Sell NFT inside the popup
        self.page.click("button.van-button--primary", timeout=10_000)
        log.info("Clicked Sell NFT in sale popup.")
        time.sleep(10)

        # Success popup
        log.info("Waiting for sale confirmation ...")
        self.page.wait_for_selector(
            "text='Selling application submitted successfully'",
            timeout=20_000
        )
        log.info("Sale submitted successfully.")
        try:
            self.page.click("button.van-button--primary", timeout=5_000)
            log.info("Clicked I understand.")
        except Exception:
            log.info("I understand button already gone — sale confirmed.")
        time.sleep(10)

        log.info("Waiting 5 minutes before next cycle ...")
        time.sleep(120)
        log.info("5 minute wait complete.")

    # ── Sell from My NFTs page ────────────────────────────────────────────────

    def sell_from_my_nfts(self) -> None:
        log.info("Navigating to My NFTs page ...")
        self.page.goto(MY_NFTS_URL, wait_until="domcontentloaded")
        time.sleep(10)

        # Close promotional popup if present
        try:
            close_btn = self.page.query_selector("div.notice-btn div:last-child")
            if close_btn and close_btn.is_visible():
                close_btn.click()
                log.info("Closed popup on My NFTs page.")
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
                    log.info("No NFTs available on My NFTs page — nothing to sell.")
                    return
        except Exception:
            pass

        # Find all Sell NFT buttons — confirmed selector: button[data-v-5055aed9]
        sell_buttons = self.page.query_selector_all("button[data-v-5055aed9]")
        if not sell_buttons:
            # Fallback
            sell_buttons = self.page.query_selector_all("button:has-text('Sell NFT')")

        if not sell_buttons:
            log.info("No Sell NFT buttons found — nothing to sell.")
            return

        log.info("Found %d NFT(s) to sell.", len(sell_buttons))
        for i, btn in enumerate(sell_buttons, 1):
            log.info("Processing NFT %d/%d ...", i, len(sell_buttons))
            try:
                btn.click()
                time.sleep(10)

                # NFT Sale popup appears — click "Sell NFT" button inside it
                # Confirmed: span.van-button__text "Sell NFT" inside button.van-button--primary
                self.page.click("button.van-button--primary", timeout=15_000)
                log.info("Clicked Sell NFT in sale popup.")
                time.sleep(10)

                # Success popup: "Selling application submitted successfully"
                self.page.wait_for_selector(
                    "text='Selling application submitted successfully'",
                    timeout=20_000
                )
                log.info("Sale submitted successfully for NFT %d.", i)
                # Click "I understand" — popup may auto-close, so don't fail if button gone
                try:
                    self.page.click("button.van-button--primary", timeout=5_000)
                    log.info("Clicked I understand.")
                except Exception:
                    log.info("I understand button already gone — sale confirmed.")
                time.sleep(10)

            except Exception as e:
                log.warning("Error selling NFT %d: %s", i, e)
                continue

        log.info("My NFTs sell step complete.")

    # ── Full single cycle ─────────────────────────────────────────────────────

    def get_reservations_available(self) -> int:
        """Navigate to reservation page and return number of reservations available today."""
        log.info("Checking reservations available today ...")
        self.page.goto(RESERVATION_URL, wait_until="domcontentloaded")
        time.sleep(10)

        # Close promotional popup if present
        try:
            close_btn = self.page.query_selector("div.notice-btn div:last-child")
            if close_btn and close_btn.is_visible():
                close_btn.click()
                log.info("Closed popup on reservation page.")
                time.sleep(10)
        except Exception:
            pass

        # Dismiss tutorial overlay if present
        try:
            skip_btn = self.page.query_selector("text='Skip'")
            if skip_btn and skip_btn.is_visible():
                skip_btn.click()
                log.info("Dismissed tutorial overlay.")
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
                            log.info("Reservations available today: %d", count)
                            return count
                except Exception:
                    continue
        except Exception as e:
            log.warning("Could not read reservation count via DOM: %s", e)

        log.warning("Could not determine reservation count — assuming 0.")
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
            log.info("NFT total (by sell buttons): %d", count)
            return count

        except Exception as e:
            log.warning("Could not read NFT total count: %s", e)
        return 0

    def ensure_nfts_cleared_before_reserve(self) -> None:
        """Before reserving, ensure NFT total is 0. Sell if needed, wait 5 min, re-check."""
        self.ensure_logged_in()
        nfts = self.get_nft_total_count()

        if nfts == 0:
            log.info("NFT total: 0 piece(s) — ready to reserve.")
            return

        log.info("%d NFT(s) found — selling before reservation ...", nfts)
        retry(self.sell_from_my_nfts, label="PreReserve:SellNFTs")
        time.sleep(10)

        log.info("Waiting 5 minutes for sale to reflect ...")
        time.sleep(120)

        self.ensure_logged_in()
        nfts = self.get_nft_total_count()
        if nfts == 0:
            log.info("NFT total now 0 — proceeding to reserve.")
            return

        log.info("%d NFT(s) still present — waiting another 5 minutes ...", nfts)
        time.sleep(120)
        self.ensure_logged_in()
        nfts = self.get_nft_total_count()
        if nfts == 0:
            log.info("NFT total now 0 — proceeding to reserve.")
        else:
            log.warning("%d NFT(s) still present after wait — proceeding anyway.", nfts)

    def get_nfts_available(self) -> int:
        """Navigate to My NFT page and return total NFT count."""
        log.info("Checking NFTs available on My NFT page ...")
        self.page.goto(MY_NFTS_URL, wait_until="domcontentloaded")
        time.sleep(10)

        # Close promotional popup if present
        try:
            close_btn = self.page.query_selector("div.notice-btn div:last-child")
            if close_btn and close_btn.is_visible():
                close_btn.click()
                log.info("Closed popup on My NFT page.")
                time.sleep(10)
        except Exception:
            pass

        try:
            page_text = self.page.inner_text("body")
            import re as _re
            match = _re.search(r"(\d+)\s*piece", page_text)
            if match:
                count = int(match.group(1))
                log.info("NFTs available: %d", count)
                return count
            # Fallback: look for sell buttons
            sell_btns = self.page.query_selector_all("button[data-v-5055aed9], button:has-text('Sell NFT')")
            count = len(sell_btns)
            log.info("NFTs available (by sell buttons): %d", count)
            return count
        except Exception as e:
            log.warning("Could not read NFT count: %s", e)

        return 0


    def _get_nft_total_number(self) -> int:
        try:
            sell_btns = self.page.query_selector_all(
                "button[data-v-5055aed9], button:has-text('Sell NFT')"
            )
            count = len(sell_btns)
            log.info("NFT total number (sell buttons): %d", count)
            return count
        except Exception as e:
            log.warning("Could not read NFT total: %s", e)
            return 0

    def ensure_no_nfts_before_reserve(self) -> None:
        log.info("Checking NFT total before reservation ...")
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
            log.info("NFT total = 0 — proceeding with reservation.")
            return
        log.info("NFT total = %d — selling before reservation ...", nft_total)
        retry(self.sell_from_my_nfts, label="PreReserve:SellNFTs")
        log.info("Waiting 5 minutes for funds to reflect ...")
        time.sleep(120)
        self.ensure_logged_in()
        log.info("Proceeding with reservation.")

    def ensure_logged_in(self) -> None:
        """Re-login if session has expired, then close popup."""
        if "/login" in self.page.url:
            log.warning("Session expired — re-logging in ...")
            retry(self.login, label="ReLogin")
            # After re-login the promotional popup always appears — close it
            time.sleep(5)
            for attempt in range(3):
                try:
                    close_btn = self.page.query_selector("div.notice-btn div:last-child")
                    if close_btn and close_btn.is_visible():
                        close_btn.click()
                        log.info("Closed popup after re-login.")
                        time.sleep(10)
                        break
                    time.sleep(5)
                except Exception:
                    time.sleep(5)

    def run_cycle(self, cycle_num: int, total_cycles: int) -> None:
        self.log.info("=== Starting cycle %d/%d ===", cycle_num, total_cycles)

        self.ensure_logged_in()

        # Sell any existing NFTs before reserving
        nfts = self.get_nfts_available()
        if nfts > 0:
            self.log.info("%d existing NFT(s) found — selling before reservation.", nfts)
            retry(self.sell_from_my_nfts, label=f"Cycle{cycle_num}:SellExisting")
            time.sleep(10)

        # Reserve and sell
        self.ensure_logged_in()
        retry(self.reserve_nft,     label=f"Cycle{cycle_num}:Reserve")
        self.ensure_logged_in()
        retry(self.sell_from_popup, label=f"Cycle{cycle_num}:SellPopup")

        self.log.info("=== Cycle %d complete. ===", cycle_num)


def main() -> None:
    validate_env()
    log.info("╔══════════════════════════════════════╗")
    log.info("║  rarbtc.com NFT Bot  —  Run started  ║")
    log.info("╚══════════════════════════════════════╝")
    log.info("Log file: %s", log_filename)

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
        bot  = RarbtcBot(page)

        try:
            retry(bot.login, label="Login")

            # Read reservations at login — this drives the number of cycles
            total_cycles = bot.get_reservations_available()
            if total_cycles == 0:
                acct_logger.info("No reservations available at login — skipping to NFT sell check.")
            else:
                total_cycles = min(total_cycles, MAX_CYCLES_CAP)
                acct_logger.info("Reservations available: %d — running %d cycle(s).", total_cycles, total_cycles)

            completed = 0
            for cycle in range(1, total_cycles + 1):
                # Re-check reservations before each cycle — stop if used up
                remaining = bot.get_reservations_available()
                if remaining == 0:
                    acct_logger.info("No reservations remaining — stopping cycles early.")
                    break
                bot.run_cycle(cycle, total_cycles)
                completed += 1

            # Always check /nft/my for any unsold NFTs after all cycles
            bot.ensure_logged_in()
            leftover = bot.get_nfts_available()
            if leftover > 0:
                acct_logger.info("%d unsold NFT(s) found after cycles — selling ...", leftover)
                retry(bot.sell_from_my_nfts, label="PostCycle:SellRemaining")

            acct_logger.info("All %d cycle(s) completed successfully.", completed)

        except Exception as exc:
            log.error("FATAL ERROR - aborting run: %s", exc, exc_info=True)
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

            # Log current URL at time of crash
            try:
                log.info("URL at crash: %s", page.url)
            except Exception:
                pass

            # Save full-page screenshot for visual debugging
            screenshot_path = logs_dir / f"error_{ts}.png"
            try:
                page.screenshot(path=str(screenshot_path), full_page=True)
                log.info("Screenshot saved: %s", screenshot_path)
            except Exception as se:
                log.warning("Could not save screenshot: %s", se)

            # Save page HTML so you can inspect real CSS selectors on the live site
            html_path = logs_dir / f"error_page_{ts}.html"
            try:
                html_path.write_text(page.content(), encoding="utf-8")
                log.info("Page HTML saved: %s - open this to find correct selectors", html_path)
            except Exception as he:
                log.warning("Could not save page HTML: %s", he)

            sys.exit(1)

        finally:
            context.close()
            browser.close()
            log.info("Browser closed. Run finished at %s UTC.", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))


if __name__ == "__main__":
    main()