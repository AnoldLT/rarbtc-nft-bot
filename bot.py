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

USERNAME             = os.environ.get("RARBTC_USERNAME", "")
PASSWORD             = os.environ.get("RARBTC_PASSWORD", "")
RESERVATION_PASSWORD = os.environ.get("RARBTC_RESERVATION_PASSWORD", "")

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

def validate_env() -> None:
    """Abort early if any required credential is missing."""
    missing = [v for v, k in [
        ("RARBTC_USERNAME",             USERNAME),
        ("RARBTC_PASSWORD",             PASSWORD),
        ("RARBTC_RESERVATION_PASSWORD", RESERVATION_PASSWORD),
    ] if not k]
    if missing:
        log.error("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)


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

        # Wait for navigation — site redirects to /home after login
        time.sleep(10)
        log.info("Post-login URL: %s", self.page.url)

        # Success = landed on /home or any non-login page
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

        # Close any promotional popup that may appear on this page too
        try:
            close_btn = self.page.query_selector("div.notice-btn div:last-child")
            if close_btn and close_btn.is_visible():
                close_btn.click()
                log.info("Closed popup on reservation page.")
                time.sleep(10)
        except Exception:
            pass

        log.info("Clicking Reservation button ...")
        # From screenshot: the button has text "Reservation" inside a blue button
        self.page.click(
            "button:has-text('Reservation'), .reservation-btn, "
            "[class*='reservation'] button, button.el-button--primary",
            timeout=20_000,
        )
        time.sleep(10)

        log.info("Waiting for reservation password popup ...")
        self.page.wait_for_selector(
            "input[type='password'], input[placeholder*='assword'], input[placeholder*='password']",
            timeout=20_000,
        )
        time.sleep(10)

        log.info("Entering reservation password ...")
        self.page.fill(
            "input[type='password'], input[placeholder*='assword']",
            RESERVATION_PASSWORD,
        )
        time.sleep(10)

        # Submit the popup
        try:
            self.page.click(
                "button:has-text('Confirm'), button:has-text('OK'), button:has-text('Submit'), "
                "button:has-text('Reservation'), button.el-button--primary",
                timeout=10_000,
            )
            log.info("Clicked confirm button on password popup.")
        except Exception:
            self.page.keyboard.press("Enter")
            log.info("Pressed Enter to submit reservation password.")
        time.sleep(10)

        log.info("Waiting up to 3 min for order confirmation popup ...")
        self.page.wait_for_selector(
            "text='Sell', text='SELL', button:has-text('Sell'), "
            "[class*='sell'], text='NFT On Sale'",
            timeout=POPUP_TIMEOUT_MS,
        )
        time.sleep(10)
        log.info("Order confirmation popup appeared.")

    # ── Sell after reservation ────────────────────────────────────────────────

    def sell_from_popup(self) -> None:
        log.info("Clicking Sell button in confirmation popup ...")
        self.page.click(
            "text='Sell', text='SELL', button:has-text('Sell'), "
            "button:has-text('SELL'), [class*='sell']",
            timeout=15_000,
        )
        time.sleep(10)

        log.info("Accepting the offered sale value ...")
        try:
            self.page.click(
                "button:has-text('Agree'), button:has-text('Accept'), button:has-text('Confirm'), "
                "button:has-text('OK'), button:has-text('Yes'), [class*='agree'], [class*='confirm']",
                timeout=10_000,
            )
            log.info("Agreed to sale value.")
        except Exception:
            log.warning("No explicit agree button found — sale may have self-confirmed.")
        time.sleep(10)

        log.info("Waiting %ds for sale to process ...", SELL_WAIT_S)
        time.sleep(SELL_WAIT_S)
        log.info("Sale from popup complete.")

    # ── Sell from My NFTs page ────────────────────────────────────────────────

    def sell_from_my_nfts(self) -> None:
        log.info("Navigating to My NFTs page ...")
        self.page.goto(MY_NFTS_URL, wait_until="domcontentloaded")
        time.sleep(10)

        # Close any popup on this page too
        try:
            close_btn = self.page.query_selector("div.notice-btn div:last-child")
            if close_btn and close_btn.is_visible():
                close_btn.click()
                log.info("Closed popup on My NFTs page.")
                time.sleep(10)
        except Exception:
            pass

        sell_buttons = self.page.query_selector_all(
            "button:has-text('Sell'), a:has-text('Sell'), [class*='sell-btn'], [class*='sell_btn']"
        )

        if not sell_buttons:
            log.info("No NFTs found on My NFTs page — nothing to sell.")
            return

        log.info("Found %d NFT(s) listed. Selling each ...", len(sell_buttons))
        for i, btn in enumerate(sell_buttons, 1):
            log.info("Selling NFT %d/%d ...", i, len(sell_buttons))
            btn.click()
            time.sleep(10)
            try:
                self.page.click(
                    "button:has-text('Agree'), button:has-text('Accept'), button:has-text('Confirm'), "
                    "button:has-text('OK'), button:has-text('Yes'), [class*='agree'], [class*='confirm']",
                    timeout=10_000,
                )
                log.info("Agreed to sale value for NFT %d.", i)
            except Exception:
                log.warning("No agree button for NFT %d — may have self-confirmed.", i)
            time.sleep(10)

        log.info("Waiting %ds for sale(s) to process ...", SELL_WAIT_S)
        time.sleep(SELL_WAIT_S)
        log.info("My NFTs sell step complete.")

    # ── Full single cycle ─────────────────────────────────────────────────────

    def run_cycle(self, cycle_num: int) -> None:
        log.info("═══ Starting cycle %d/%d ═══", cycle_num, MAX_CYCLES)

        retry(self.reserve_nft,      label=f"Cycle{cycle_num}:Reserve")
        retry(self.sell_from_popup,  label=f"Cycle{cycle_num}:SellPopup")
        retry(self.sell_from_my_nfts, label=f"Cycle{cycle_num}:SellMyNFTs")

        log.info("═══ Cycle %d complete. ═══", cycle_num)


# ── Entry point ───────────────────────────────────────────────────────────────

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

            for cycle in range(1, MAX_CYCLES + 1):
                bot.run_cycle(cycle)

            log.info("✅ All %d cycles completed successfully.", MAX_CYCLES)

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
