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
        self.ensure_enough_balance()

        # Re-navigate to reservation page in case ensure_enough_balance redirected
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
        # After clicking Sell NFT in the reservation success popup,
        # the NFT Sale popup appears with price details and a Sell NFT button
        log.info("Waiting for NFT Sale popup ...")
        # Confirmed: button.van-button--primary with span.van-button__text "Sell NFT"
        self.page.wait_for_selector(
            "text='NFT Sale'",
            timeout=20_000
        )
        time.sleep(5)
        log.info("NFT Sale popup appeared — clicking Sell NFT ...")
        self.page.click("button.van-button--primary", timeout=10_000)
        time.sleep(10)

        # Success popup: "Selling application submitted successfully"
        # Click "I understand" — confirmed: button.van-button--primary
        log.info("Waiting for sale confirmation ...")
        self.page.wait_for_selector(
            "text='Selling application submitted successfully'",
            timeout=20_000
        )
        log.info("Sale submitted successfully.")
        self.page.click("button.van-button--primary", timeout=10_000)
        log.info("Clicked I understand.")
        time.sleep(10)

        log.info("Waiting 10 minutes before next cycle ...")
        time.sleep(600)
        log.info("10 minute wait complete.")

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
                # Click "I understand" — same button class: button.van-button--primary
                self.page.wait_for_selector(
                    "text='Selling application submitted successfully'",
                    timeout=20_000
                )
                log.info("Sale submitted successfully for NFT %d.", i)
                self.page.click("button.van-button--primary", timeout=10_000)
                log.info("Clicked I understand.")
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

        # Read reservation count — div.val contains "X Times"
        try:
            page_text = self.page.inner_text("body")
            import re as _re
            match = _re.search(r"(\d+)\s*Times\s*\n.*?Number of reservations available", page_text, _re.DOTALL)
            if match:
                count = int(match.group(1))
                log.info("Reservations available today: %d", count)
                return count
            # Fallback: find all div.val elements
            val_els = self.page.query_selector_all("div.val")
            for el in val_els:
                txt = el.inner_text().strip()
                if "Times" in txt:
                    count = int(txt.replace("Times", "").strip())
                    log.info("Reservations available today: %d", count)
                    return count
        except Exception as e:
            log.warning("Could not read reservation count: %s", e)

        log.warning("Could not determine reservation count — assuming 0.")
        return 0

    def get_amount_available(self) -> float:
        """Read the Amount available for reservation from the reservation page."""
        try:
            page_text = self.page.inner_text("body")
            import re as _re
            # Looks for pattern like "250 USDT\nAmount available for reservation"
            match = _re.search(r"([\d.]+)\s*USDT[\s\S]*?Amount available for reservation", page_text)
            if match:
                amount = float(match.group(1))
                log.info("Amount available for reservation: %.2f USDT", amount)
                return amount
            # Fallback: find all div.val elements
            val_els = self.page.query_selector_all("div.val")
            for el in val_els:
                txt = el.inner_text().strip()
                if "USDT" in txt and "." in txt:
                    try:
                        amount = float(txt.replace("USDT", "").strip())
                        log.info("Amount available for reservation: %.2f USDT", amount)
                        return amount
                    except ValueError:
                        continue
        except Exception as e:
            log.warning("Could not read amount available: %s", e)
        log.warning("Could not determine amount available — assuming 0.")
        return 0.0

    def ensure_enough_balance(self) -> None:
        """
        Before reserving, check amount available is >= 250 USDT.
        If not, sell any existing NFTs and wait 10 min for funds to reflect.
        Then proceed regardless (even if still below 250).
        """
        amount = self.get_amount_available()
        if amount >= 250:
            log.info("Amount available %.2f USDT >= 250 — proceeding with reservation.", amount)
            return

        log.warning("Amount available %.2f USDT < 250 — checking My NFT page for unsold NFTs ...", amount)
        self.ensure_logged_in()
        nfts = self.get_nfts_available()

        if nfts > 0:
            log.info("%d NFT(s) found — selling to free up balance ...", nfts)
            retry(self.sell_from_my_nfts, label="BalanceCheck:SellNFTs")
        else:
            log.info("No NFTs to sell — will wait 10 min and proceed anyway.")

        log.info("Waiting 10 minutes for funds to reflect ...")
        time.sleep(600)

        # Re-check amount after wait
        self.ensure_logged_in()
        self.page.goto(RESERVATION_URL, wait_until="domcontentloaded")
        time.sleep(10)
        amount = self.get_amount_available()
        if amount >= 250:
            log.info("Amount now %.2f USDT >= 250 — proceeding.", amount)
        else:
            log.warning("Amount still %.2f USDT < 250 after wait — proceeding anyway.", amount)

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
            match = _re.search(r"(\d+)\s*piece\s*\n.*?NFT total number", page_text, _re.DOTALL)
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

    def run_cycle(self, cycle_num: int) -> None:
        log.info("═══ Starting cycle %d/%d ═══", cycle_num, MAX_CYCLES)

        self.ensure_logged_in()

        # Check reservations available FIRST on every cycle
        reservations = self.get_reservations_available()

        if reservations == 0:
            log.info("No reservations available — checking My NFT page for unsold NFTs.")
            self.ensure_logged_in()
            nfts = self.get_nfts_available()
            if nfts > 0:
                log.info("%d NFT(s) found — selling ...", nfts)
                retry(self.sell_from_my_nfts, label=f"Cycle{cycle_num}:SellMyNFTs")
            else:
                log.info("No NFTs to sell either — cycle complete.")

        elif reservations == 1:
            log.info("1 reservation available — selling existing NFTs first, then reserving.")
            # Step 1: Sell any existing NFTs first
            self.ensure_logged_in()
            nfts = self.get_nfts_available()
            if nfts > 0:
                log.info("%d existing NFT(s) found — selling before reservation.", nfts)
                retry(self.sell_from_my_nfts, label=f"Cycle{cycle_num}:SellExisting")
                time.sleep(10)
            # Step 2: Do the 1 reservation and sell
            self.ensure_logged_in()
            retry(self.reserve_nft,     label=f"Cycle{cycle_num}:Reserve")
            self.ensure_logged_in()
            retry(self.sell_from_popup, label=f"Cycle{cycle_num}:SellPopup")

        else:
            log.info("Reservations available: %d — proceeding with reserve + sell.", reservations)
            # Step 1: Sell any existing NFTs first
            self.ensure_logged_in()
            nfts = self.get_nfts_available()
            if nfts > 0:
                log.info("%d existing NFT(s) found — selling before reservation.", nfts)
                retry(self.sell_from_my_nfts, label=f"Cycle{cycle_num}:SellExisting")
                time.sleep(10)
            # Step 2: Reserve and sell
            self.ensure_logged_in()
            retry(self.reserve_nft,     label=f"Cycle{cycle_num}:Reserve")
            self.ensure_logged_in()
            retry(self.sell_from_popup, label=f"Cycle{cycle_num}:SellPopup")

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
