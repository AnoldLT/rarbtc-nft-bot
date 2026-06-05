"""
Rarzz NFT Trading Bot
Automates daily NFT reservation and sell cycles on rarbtc.com
Supports N accounts sequentially. Sends SendGrid email report after all accounts.
"""

import os
import time
import logging
from datetime import datetime, date

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

load_dotenv()

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def get_logger(name: str, log_path: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(fh)
        logger.addHandler(sh)
    return logger


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

def get_account_count() -> int:
    val = os.environ.get("ACCOUNT_COUNT", "1").strip()
    try:
        count = int(val)
    except ValueError:
        count = 1
    return max(1, count)


def get_account_credentials(n: int) -> dict | None:
    username = os.environ.get(f"RARBTC_USERNAME_{n}", "").strip()
    password = os.environ.get(f"RARBTC_PASSWORD_{n}", "").strip()
    reservation_password = os.environ.get(f"RARBTC_RESERVATION_PASSWORD_{n}", "").strip()
    if not username or not password or not reservation_password:
        return None
    return {
        "username": username,
        "password": password,
        "reservation_password": reservation_password,
    }


# ---------------------------------------------------------------------------
# Bot class
# ---------------------------------------------------------------------------

class RarbtcBot:
    BASE_URL = "https://rarbtc.com"

    def __init__(self, page, username: str, password: str, reservation_password: str,
                 account_num: int, logger: logging.Logger):
        self.page = page
        self.username = username
        self.password = password
        self.reservation_password = reservation_password
        self.account_num = account_num
        self.log = logger

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _goto(self, path: str):
        url = self.BASE_URL + path if path.startswith("/") else path
        self.log.info(f"Navigating to {url}")
        self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        time.sleep(2)

    def _close_popup(self):
        """Dismiss the promotional popup that appears on every page load."""
        try:
            btn = self.page.locator("div.notice-btn div:last-child")
            btn.wait_for(timeout=5_000)
            btn.click()
            self.log.info("Promotional popup dismissed")
            time.sleep(1)
        except Exception:
            pass  # No popup present

    def _dismiss_tutorial(self):
        """Dismiss tutorial overlay if present."""
        try:
            skip = self.page.get_by_text("Skip", exact=True)
            skip.wait_for(timeout=3_000)
            skip.click()
            self.log.info("Tutorial overlay dismissed")
            time.sleep(1)
        except Exception:
            pass

    def _is_on_login_page(self) -> bool:
        return "/login" in self.page.url

    # -----------------------------------------------------------------------
    # Login
    # -----------------------------------------------------------------------

    def login(self):
        self.log.info(f"[Account {self.account_num}] Logging in as {self.username}")
        self._goto("/login")

        # Dismiss cookie consent
        try:
            self.page.locator("button.accept-btn").click(timeout=6_000)
            self.log.info("Cookie consent dismissed")
            time.sleep(1)
        except Exception:
            pass

        # Click email login tab
        self.page.locator("#tab-0").click(timeout=10_000)
        time.sleep(1)

        # Fill credentials
        self.page.locator("input[placeholder='Please enter your email']").fill(self.username)
        self.page.locator(
            "input[placeholder='Password must be 8-20 characters or more']"
        ).fill(self.password)

        # Click login
        self.page.locator("div.bt.flex-center").click(timeout=10_000)

        # Wait for redirect away from /login
        self.page.wait_for_url(lambda url: "/login" not in url, timeout=20_000)
        self.log.info("Login successful — redirected to home")
        time.sleep(2)

        # Close promotional popup
        self._close_popup()

    def ensure_logged_in(self):
        """Re-login if session has expired."""
        if self._is_on_login_page():
            self.log.warning("Session expired — re-logging in")
            self.login()

    # -----------------------------------------------------------------------
    # Stats helpers
    # -----------------------------------------------------------------------

    def get_reservations_available(self) -> int:
        """Read reservation count from reservation page."""
        self._goto("/nft/reservation")
        self._close_popup()
        self._dismiss_tutorial()

        try:
            items = self.page.locator("li").all()
            for item in items:
                text = item.inner_text()
                if "Number of reservations available today" in text:
                    val_text = item.locator("div.val").inner_text(timeout=5_000).strip()
                    count = int("".join(filter(str.isdigit, val_text)) or "0")
                    self.log.info(f"Reservations available: {count}")
                    return min(count, 10)  # Safety cap
        except Exception as e:
            self.log.warning(f"Could not read reservations: {e}")

        return 0

    def get_nfts_available(self) -> int:
        """Count unsold NFTs on /nft/my."""
        self._goto("/nft/my")
        self._close_popup()
        try:
            buttons = self.page.locator("button[data-v-5055aed9]").all()
            count = len(buttons)
            self.log.info(f"NFTs available (sell buttons): {count}")
            return count
        except Exception as e:
            self.log.warning(f"Could not count NFTs: {e}")
            return 0

    def _get_nft_total_number(self) -> int:
        """Count sell buttons on current page (must already be on /nft/my)."""
        try:
            return len(self.page.locator("button[data-v-5055aed9]").all())
        except Exception:
            return 0

    def get_today_reservation_income(self) -> str:
        """Read day income from /person/myIncome."""
        self.log.info("Reading today's reservation income")
        self._goto("/person/myIncome")
        self._close_popup()
        try:
            info_divs = self.page.locator("div.info").all()
            for div in info_divs:
                text = div.inner_text()
                if "personal reservation income" in text.lower():
                    num = div.locator("div.num").inner_text(timeout=5_000).strip()
                    self.log.info(f"Today's income: {num}")
                    return num
        except Exception as e:
            self.log.warning(f"Could not read income: {e}")
        return "$0"

    # -----------------------------------------------------------------------
    # Sell helpers
    # -----------------------------------------------------------------------

    def sell_from_my_nfts(self):
        """Sell all NFTs on /nft/my. Reloads page each attempt to clear stuck popups."""
        self.log.info("Selling NFTs from /nft/my")

        max_attempts = 10
        attempt = 0
        while attempt < max_attempts:
            attempt += 1

            # Fresh page load every attempt — clears any stuck popup from previous attempt
            self._goto("/nft/my")
            self._close_popup()
            time.sleep(2)

            buttons = self.page.locator("button[data-v-5055aed9]").all()
            if not buttons:
                self.log.info("No sell buttons found — done selling")
                break

            self.log.info(f"Sell attempt {attempt} — {len(buttons)} sell button(s) visible")

            try:
                buttons[0].click(timeout=10_000)
                self.log.info("Clicked sell button")

                # Wait for NFT Sale popup
                self.page.get_by_text("NFT Sale", exact=True).wait_for(timeout=20_000)
                self.log.info("NFT Sale popup appeared")

                # Confirm sell
                self.page.locator("button.van-button--primary").click(timeout=10_000)
                self.log.info("Confirmed sell — waiting 10s for processing")
                time.sleep(10)

                # Dismiss any remaining popup (success or I understand)
                try:
                    self.page.locator("button.van-button--primary").click(timeout=3_000)
                    self.log.info("Dismissed post-sell popup")
                except Exception:
                    pass

                self._close_popup()
                time.sleep(3)
                # Loop continues — next iteration reloads /nft/my and recounts

            except PlaywrightTimeoutError as e:
                self.log.warning(f"Sell attempt {attempt} timeout: {e}")
                # Don't break — reload and retry
                continue
            except Exception as e:
                self.log.warning(f"Sell attempt {attempt} error: {e}")
                continue
        else:
            self.log.warning("Max sell attempts reached")

    def ensure_no_nfts_before_reserve(self):
        """If NFTs exist on /nft/my, sell them before reserving."""
        self._goto("/nft/my")
        self._close_popup()
        count = self._get_nft_total_number()
        if count > 0:
            self.log.info(f"{count} unsold NFTs found before reserve — selling first")
            self.sell_from_my_nfts()
            self.log.info("Waiting 2 min after pre-reserve sell")
            time.sleep(120)
        else:
            self.log.info("No unsold NFTs — proceeding to reservation")

    # -----------------------------------------------------------------------
    # Reservation flow
    # -----------------------------------------------------------------------

    def reserve_nft(self):
        """Full NFT reservation flow."""
        self.log.info("Starting reservation flow")
        self._goto("/nft/reservation")
        self._close_popup()
        self._dismiss_tutorial()

        self.ensure_no_nfts_before_reserve()

        # Back to reservation page
        self._goto("/nft/reservation")
        self._close_popup()

        # Click reservation button
        try:
            self.page.locator("button.one-bt").click(timeout=10_000)
            self.log.info("Clicked reservation button")
        except PlaywrightTimeoutError:
            self.log.warning("button.one-bt not found — reservations may be exhausted")
            return

        # Wait for fund password popup
        try:
            self.page.locator("div.pw input[type='text']").wait_for(timeout=15_000)
        except PlaywrightTimeoutError:
            self.log.warning("Fund password popup did not appear")
            return

        # Click the input directly — clicking parent div is blocked by the input intercepting events
        pin_input = self.page.locator("div.pw input[type='text']")
        pin_input.click(timeout=5_000)
        time.sleep(0.5)
        pin_input.fill(self.reservation_password)
        self.log.info("Fund password filled")

        # Confirm
        self.page.locator("button.van-button--primary").click(timeout=10_000)
        self.log.info("Confirmed reservation — waiting up to 3 min for success popup")

        # Wait for Reservation Successful (up to 3 min)
        try:
            self.page.get_by_text("Reservation Successful", exact=True).wait_for(timeout=180_000)
            self.log.info("Reservation Successful popup appeared")
        except PlaywrightTimeoutError:
            self.log.warning("Reservation Successful popup did not appear within 3 min")
            return

        # Click Sell NFT button in popup (last-child)
        try:
            self.page.locator("div.but button:last-child").click(timeout=10_000)
            self.log.info("Clicked Sell NFT in reservation popup")
        except Exception as e:
            self.log.warning(f"Could not click Sell NFT in popup: {e}")

    # -----------------------------------------------------------------------
    # Sell from popup flow
    # -----------------------------------------------------------------------

    def sell_from_popup(self):
        """Force navigate to /nft/my and sell. Reuses sell_from_my_nfts for consistency."""
        self.log.info("sell_from_popup: delegating to sell_from_my_nfts")
        self.sell_from_my_nfts()

        # 2-minute pause between cycles
        self.log.info("Sleeping 2 min between cycles")
        time.sleep(120)

    # -----------------------------------------------------------------------
    # Cycle
    # -----------------------------------------------------------------------

    def run_cycle(self, cycle_num: int, total_cycles: int):
        self.log.info(f"--- Cycle {cycle_num}/{total_cycles} ---")

        self.ensure_logged_in()

        # Sell existing NFTs first
        count = self.get_nfts_available()
        if count > 0:
            self.sell_from_my_nfts()
            time.sleep(10)

        self.ensure_logged_in()
        self.reserve_nft()

        self.ensure_logged_in()
        self.sell_from_popup()


# ---------------------------------------------------------------------------
# Per-account runner
# ---------------------------------------------------------------------------

def run_account(account_num: int) -> dict:
    os.makedirs("logs", exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    today_str = date.today().strftime("%Y-%m-%d")
    today_compact = date.today().strftime("%Y%m%d")

    log_path = f"logs/account_{account_num}_bot_{timestamp}.log"
    logger = get_logger(f"account_{account_num}", log_path)
    logger.info(f"=== Account {account_num} starting ===")

    creds = get_account_credentials(account_num)
    if not creds:
        logger.warning(f"Credentials missing for account {account_num} — skipping")
        summary = {
            "account": account_num,
            "status": "SKIPPED",
            "reason": "Missing credentials",
            "reservations_at_login": 0,
            "nfts_at_login": 0,
            "reservations_after": 0,
            "nfts_after": 0,
            "income": "$0",
        }
        _write_report(account_num, today_str, today_compact, summary, logger)
        return summary

    summary = {
        "account": account_num,
        "status": "SUCCESS",
        "reason": "",
        "reservations_at_login": 0,
        "nfts_at_login": 0,
        "reservations_after": 0,
        "nfts_after": 0,
        "income": "$0",
    }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        page = ctx.new_page()

        try:
            bot = RarbtcBot(
                page=page,
                username=creds["username"],
                password=creds["password"],
                reservation_password=creds["reservation_password"],
                account_num=account_num,
                logger=logger,
            )

            # Login
            bot.login()

            # Opening stats
            res_at_login = bot.get_reservations_available()
            nfts_at_login = bot.get_nfts_available()
            summary["reservations_at_login"] = res_at_login
            summary["nfts_at_login"] = nfts_at_login
            logger.info(f"Opening stats — reservations: {res_at_login}, NFTs: {nfts_at_login}")

            if res_at_login == 0:
                logger.warning("No reservations available — skipping cycles")
                summary["status"] = "SKIPPED"
                summary["reason"] = "No reservations available at login"
            else:
                # Main cycle loop
                total = res_at_login
                for cycle in range(1, total + 1):
                    # Re-check reservations before each cycle
                    bot.ensure_logged_in()
                    current_res = bot.get_reservations_available()
                    if current_res == 0:
                        logger.info("Reservations exhausted — breaking cycle loop early")
                        break
                    bot.run_cycle(cycle, total)

                # Sell any leftover NFTs
                logger.info("Post-cycle: checking for leftover unsold NFTs")
                bot.ensure_logged_in()
                leftover = bot.get_nfts_available()
                if leftover > 0:
                    logger.info(f"{leftover} leftover NFTs — selling")
                    bot.sell_from_my_nfts()

            # Closing stats
            bot.ensure_logged_in()
            res_after = bot.get_reservations_available()
            nfts_after = bot.get_nfts_available()
            summary["reservations_after"] = res_after
            summary["nfts_after"] = nfts_after

            # Wait for settlement before reading income
            if nfts_after > 0:
                logger.info("NFTs still present — attempting final sell before settlement wait")
                bot.sell_from_my_nfts()
                logger.info("Waiting 2 min for sales to settle")
                time.sleep(120)
                nfts_after = bot.get_nfts_available()
                summary["nfts_after"] = nfts_after
            else:
                logger.info("Waiting 2 min for funds to settle")
                time.sleep(120)

            # Read income
            bot.ensure_logged_in()
            income = bot.get_today_reservation_income()
            summary["income"] = income

        except Exception as e:
            logger.error(f"Unhandled error for account {account_num}: {e}", exc_info=True)
            summary["status"] = "FAILED"
            summary["reason"] = str(e)

        finally:
            ctx.close()
            browser.close()

    _write_report(account_num, today_str, today_compact, summary, logger)
    logger.info(f"=== Account {account_num} complete — status: {summary['status']} ===")
    return summary


def _write_report(account_num: int, today_str: str, today_compact: str,
                  summary: dict, logger: logging.Logger):
    os.makedirs("logs", exist_ok=True)
    report_path = f"logs/account_{account_num}_report_{today_compact}.log"
    lines = [
        "=" * 50,
        f"  ACCOUNT {account_num} — Daily Report",
        f"  Date: {today_str}",
        f"  Status: {summary['status']}",
        "=" * 50,
        f"  Reservations at login:          {summary['reservations_at_login']}",
        f"  NFTs available at login:        {summary['nfts_at_login']}",
        f"  Reservations after run:         {summary['reservations_after']}",
        f"  NFTs unsold after run:          {summary['nfts_after']}",
        f"  Day income:                     {summary['income']}",
        "=" * 50,
    ]
    if summary.get("reason"):
        lines.insert(-1, f"  Reason:                         {summary['reason']}")

    report_text = "\n".join(lines)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text + "\n")
    logger.info(f"Report written to {report_path}")


# ---------------------------------------------------------------------------
# Email notification
# ---------------------------------------------------------------------------

def send_run_notification(all_summaries: list[dict]):
    sendgrid_key = os.environ.get("SENDGRID_API_KEY", "").strip()
    to_email = os.environ.get("NOTIFY_EMAIL_TO", "").strip()
    from_email = os.environ.get("NOTIFY_EMAIL_FROM", "").strip()

    if not sendgrid_key or not to_email or not from_email:
        print("SendGrid not configured — skipping email notification")
        return

    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail

        today_str = date.today().strftime("%Y-%m-%d")
        subject = f"Rarzz NFT Bot — Daily Report {today_str}"

        html_parts = [f"<h2>Rarzz NFT Bot — {today_str}</h2>"]
        for s in all_summaries:
            status_color = "#27ae60" if s["status"] == "SUCCESS" else "#e74c3c"
            html_parts.append(f"""
<hr>
<h3>Account {s['account']} — <span style="color:{status_color}">{s['status']}</span></h3>
<table>
  <tr><td>Reservations at login:</td><td><b>{s['reservations_at_login']}</b></td></tr>
  <tr><td>NFTs available at login:</td><td><b>{s['nfts_at_login']}</b></td></tr>
  <tr><td>Reservations after run:</td><td><b>{s['reservations_after']}</b></td></tr>
  <tr><td>NFTs unsold after run:</td><td><b>{s['nfts_after']}</b></td></tr>
  <tr><td>Day income:</td><td><b>{s['income']}</b></td></tr>
  {f"<tr><td>Reason:</td><td>{s['reason']}</td></tr>" if s.get('reason') else ""}
</table>
""")

        html_content = "\n".join(html_parts)
        message = Mail(
            from_email=from_email,
            to_emails=to_email,
            subject=subject,
            html_content=html_content,
        )
        sg = sendgrid.SendGridAPIClient(api_key=sendgrid_key)
        response = sg.send(message)
        print(f"Email sent — status {response.status_code}")
    except Exception as e:
        print(f"Failed to send email: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def validate_env():
    count_str = os.environ.get("ACCOUNT_COUNT", "").strip()
    if not count_str:
        raise EnvironmentError("ACCOUNT_COUNT is not set. Set it as a GitHub Actions variable or in .env")
    try:
        count = int(count_str)
        if count < 1:
            raise ValueError
    except ValueError:
        raise EnvironmentError(f"ACCOUNT_COUNT must be a positive integer, got: {count_str!r}")
    return count


def main():
    print("=== Rarzz NFT Trading Bot starting ===")

    count = validate_env()
    print(f"Running {count} account(s)")

    # Check email config
    if not os.environ.get("SENDGRID_API_KEY"):
        print("Note: SENDGRID_API_KEY not set — email notifications disabled")

    all_summaries = []
    for account_num in range(1, count + 1):
        print(f"\n--- Starting account {account_num}/{count} ---")
        summary = run_account(account_num)
        all_summaries.append(summary)

    print("\n=== All accounts complete — sending email report ===")
    send_run_notification(all_summaries)
    print("=== Done ===")


if __name__ == "__main__":
    main()