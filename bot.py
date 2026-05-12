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

        # Site is JS-rendered — wait up to 15s for ANY input to appear before proceeding
        log.info("Waiting for login form to render ...")
        try:
            self.page.wait_for_selector("input", timeout=15_000)
            log.info("Input field detected on page.")
        except PlaywrightTimeoutError:
            log.warning("No input fields found after 15s — page may not have loaded correctly.")

        time.sleep(2)  # Extra buffer for slow JS hydration

        # Log all inputs found on the page for debugging
        inputs = self.page.query_selector_all("input")
        log.info("Found %d input(s) on login page:", len(inputs))
        for inp in inputs:
            itype = inp.get_attribute("type") or ""
            iname = inp.get_attribute("name") or ""
            iid   = inp.get_attribute("id") or ""
            iph   = inp.get_attribute("placeholder") or ""
            icls  = inp.get_attribute("class") or ""
            log.info("  input type=%r name=%r id=%r placeholder=%r class=%r", itype, iname, iid, iph, icls)

        # Try username field — broad ordered list of selectors
        USERNAME_SELECTORS = [
            "input[name='username']",
            "input[name='email']",
            "input[name='user']",
            "input[name='login']",
            "input[name='account']",
            "input[type='email']",
            "input[type='text']",
            "input[id*='user']",
            "input[id*='email']",
            "input[id*='login']",
            "input[placeholder*='user' i]",
            "input[placeholder*='email' i]",
            "input[placeholder*='account' i]",
            "input[placeholder*='login' i]",
        ]

        username_filled = False
        for sel in USERNAME_SELECTORS:
            try:
                el = self.page.query_selector(sel)
                if el and el.is_visible():
                    el.fill(USERNAME)
                    log.info("Filled username using selector: %s", sel)
                    username_filled = True
                    break
            except Exception:
                continue

        if not username_filled:
            raise RuntimeError("Could not find username input field — check error_page HTML in artifacts for correct selector")

        # Try password field
        PASSWORD_SELECTORS = [
            "input[type='password']",
            "input[name='password']",
            "input[name='pass']",
            "input[name='pwd']",
            "input[id*='pass']",
            "input[id*='pwd']",
            "input[placeholder*='pass' i]",
            "input[placeholder*='pwd' i]",
        ]

        password_filled = False
        for sel in PASSWORD_SELECTORS:
            try:
                el = self.page.query_selector(sel)
                if el and el.is_visible():
                    el.fill(PASSWORD)
                    log.info("Filled password using selector: %s", sel)
                    password_filled = True
                    break
            except Exception:
                continue

        if not password_filled:
            raise RuntimeError("Could not find password input field — check error_page HTML in artifacts for correct selector")

        # Submit the form
        SUBMIT_SELECTORS = [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Login')",
            "button:has-text('Log In')",
            "button:has-text('Sign In')",
            "button:has-text('Sign in')",
            "button:has-text('Submit')",
            "a:has-text('Login')",
            "[class*='login-btn']",
            "[class*='login_btn']",
            "[class*='btn-login']",
            "[class*='submit']",
        ]

        submitted = False
        for sel in SUBMIT_SELECTORS:
            try:
                el = self.page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    log.info("Submitted login using selector: %s", sel)
                    submitted = True
                    break
            except Exception:
                continue

        if not submitted:
            # Last resort: press Enter on the password field
            log.warning("No submit button found — pressing Enter on password field")
            self.page.keyboard.press("Enter")

        # Wait for page to change after login
        time.sleep(5)
        log.info("Post-login URL: %s", self.page.url)

        if "/login" in self.page.url or "/signin" in self.page.url:
            raise RuntimeError("Login failed — still on login page. Check credentials in GitHub Secrets.")

        log.info("Login successful.")

    # ── Reserve NFT ───────────────────────────────────────────────────────────

    def reserve_nft(self) -> None:
        log.info("Navigating to reservation page …")
        self.page.goto(RESERVATION_URL, wait_until="networkidle")

        log.info("Clicking Reserve button …")
        self.page.click(
            "button:has-text('Reserve'), a:has-text('Reserve'), [class*='reserve']",
            timeout=20_000,
        )

        log.info("Waiting for reservation password popup …")
        self.page.wait_for_selector(
            "input[type='password'], input[placeholder*='password'], input[placeholder*='Password']",
            timeout=20_000,
        )

        log.info("Entering reservation password …")
        self.page.fill(
            "input[type='password'], input[placeholder*='password'], input[placeholder*='Password']",
            RESERVATION_PASSWORD,
        )
        # Submit the popup form
        self.page.keyboard.press("Enter")
        # Alternative: click a confirm/OK button inside the popup
        try:
            self.page.click(
                "button:has-text('Confirm'), button:has-text('OK'), button:has-text('Submit'), "
                "button:has-text('Reserve'), [class*='confirm']",
                timeout=5_000,
            )
        except Exception:
            pass  # Enter key above was sufficient

        log.info("Waiting up to 3 min for order confirmation popup …")
        # Wait for the confirmation popup containing 'SELL NFT' or similar text
        self.page.wait_for_selector(
            "text='SELL NFT', text='Sell NFT', button:has-text('Sell'), [class*='sell']",
            timeout=POPUP_TIMEOUT_MS,
        )
        log.info("Order confirmation popup appeared.")

    # ── Sell after reservation ────────────────────────────────────────────────

    def sell_from_popup(self) -> None:
        log.info("Clicking Sell NFT in confirmation popup …")
        self.page.click(
            "text='SELL NFT', text='Sell NFT', button:has-text('Sell NFT'), "
            "button:has-text('SELL NFT'), [class*='sell-nft']",
            timeout=15_000,
        )

        log.info("Accepting the offered sale value …")
        # Agree / confirm the sale value dialog
        try:
            self.page.click(
                "button:has-text('Agree'), button:has-text('Accept'), button:has-text('Confirm'), "
                "button:has-text('OK'), button:has-text('Yes'), [class*='agree'], [class*='confirm']",
                timeout=10_000,
            )
        except Exception:
            log.warning("No explicit agree button found — sale may have self-confirmed.")

        log.info("Waiting %ds for sale to process …", SELL_WAIT_S)
        time.sleep(SELL_WAIT_S)
        log.info("Sale from popup complete.")

    # ── Sell from My NFTs page ────────────────────────────────────────────────

    def sell_from_my_nfts(self) -> None:
        log.info("Navigating to My NFTs page …")
        self.page.goto(MY_NFTS_URL, wait_until="networkidle")

        # Check whether any NFTs are listed
        sell_buttons = self.page.query_selector_all(
            "button:has-text('Sell'), a:has-text('Sell'), [class*='sell-btn'], [class*='sell_btn']"
        )

        if not sell_buttons:
            log.info("No NFTs found on My NFTs page — nothing to sell.")
            return

        log.info("Found %d NFT(s) listed. Selling each …", len(sell_buttons))
        for i, btn in enumerate(sell_buttons, 1):
            log.info("Selling NFT %d/%d …", i, len(sell_buttons))
            btn.click()
            time.sleep(2)
            # Agree to the offered sale value
            try:
                self.page.click(
                    "button:has-text('Agree'), button:has-text('Accept'), button:has-text('Confirm'), "
                    "button:has-text('OK'), button:has-text('Yes'), [class*='agree'], [class*='confirm']",
                    timeout=10_000,
                )
            except Exception:
                log.warning("No agree button for NFT %d — may have self-confirmed.", i)
            time.sleep(5)  # brief pause between sells

        log.info("Waiting %ds for sale(s) to process …", SELL_WAIT_S)
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
