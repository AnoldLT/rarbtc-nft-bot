# Rarzz NFT Trading Bot

Fully automated, cloud-hosted NFT buy-and-sell bot for [rarbtc.com](https://rarbtc.com) (Rarzz NFT platform).
Runs daily at **03:23 UTC (05:23 SAST)** via **GitHub Actions** — no local machine required.
Supports **N accounts** running sequentially in one daily job.
Sends a **daily email report** via SendGrid after all accounts complete.

---

## Purpose

This bot automates the daily NFT reservation and sale cycle on rarbtc.com. Each account is allowed a fixed number of reservations per 24-hour period (typically 2–3 depending on membership level). The bot:
- Logs in, checks how many reservations are available
- Runs exactly that many buy-sell cycles
- Sells any leftover unsold NFTs after all cycles
- Reads the day's income from the platform's own income page
- Sends a summary email and writes a per-account report log

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.11 |
| Browser automation | Playwright (headless Chromium) |
| Scheduling | GitHub Actions cron |
| Credential storage | GitHub Secrets + GitHub Actions Variables |
| Email notifications | SendGrid |
| Dependencies | `playwright==1.44.0`, `python-dotenv==1.0.1`, `sendgrid==6.11.0` |

---

## Repository Structure

```
rarbtc-nft-bot/
├── .github/
│   └── workflows/
│       └── nft_bot.yml        # GitHub Actions schedule and runner config
├── bot.py                     # Main automation script
├── requirements.txt           # Python dependencies
├── .env.example               # Credential template (safe to commit)
├── .gitignore                 # Blocks .env, logs, screenshots
├── SECURITY.md                # Security policy
└── README.md                  # This file
```

---

## Full Bot Flow (What It Does)

### Startup — once per account
1. Read `ACCOUNT_COUNT` — loop through each account sequentially
2. Load credentials for account N (`RARBTC_USERNAME_N` etc.)
3. If secrets missing → skip account, write skip log, continue to next
4. Launch headless Chromium browser
5. Navigate to `https://rarbtc.com/login`
6. Dismiss cookie consent popup (`button.accept-btn`)
7. Click Email login tab (`#tab-0`)
8. Fill email (`input[placeholder='Please enter your email']`)
9. Fill password (`input[placeholder='Password must be 8-20 characters or more']`)
10. Click login button (`div.bt.flex-center`)
11. Wait for URL redirect away from `/login` using `page.wait_for_url()`
12. Close promotional popup (`div.notice-btn div:last-child`)
13. **Collect opening stats:** reservations available, NFTs available

### Cycle loop — driven by reservations available
14. Call `get_reservations_available()` → returns integer (e.g. 2 or 3)
15. Apply safety cap of 10
16. Loop that many times:
    - Re-check reservations before each cycle — if 0, break early
    - **Sell existing NFTs first** (if any on `/nft/my`)
    - **Reserve NFT** (full flow below)
    - **Sell reserved NFT** (full flow below)
    - Wait **2 minutes** between cycles
17. After all cycles — check `/nft/my` for any leftover unsold NFTs and sell them

### Reserve NFT flow
1. Navigate to `/nft/reservation`
2. Close promotional popup if present
3. Dismiss tutorial overlay (`text='Skip'`) if present
4. Check NFT total on `/nft/my` — if > 0, sell them first, wait 2 min
5. Navigate back to `/nft/reservation`
6. Click `button.one-bt` (Reservation button)
7. Wait for fund password popup (`div.pw input[type='text']`)
8. Click `div.van-password-input` to focus the PIN field
9. Fill hidden input with reservation password
10. Click `button.van-button--primary` (Confirm)
11. Wait up to **3 minutes** for `text='Reservation Successful'` popup
12. Click `div.but button:last-child` (Sell NFT button in popup)

### Sell NFT flow (after reservation)
1. Force navigate to `https://rarbtc.com/nft/my`
   - *(Site may redirect to `/nft/reservation/list?id=X` — always override)*
2. Close popup if present
3. Find `button[data-v-5055aed9]` (Sell NFT button)
4. Click it → wait for `text='NFT Sale'` popup (20s timeout)
5. Click `button.van-button--primary` (Sell NFT inside popup)
6. Wait for `text='Selling application submitted successfully'`
7. Click `button.van-button--primary` (I understand) — graceful if auto-closed

### Sell from My NFT page (standalone)
Same as above but loops through all sell buttons found on the page.

### Closing stats — after all cycles
1. Check reservations remaining
2. Navigate to `/nft/my` — count unsold NFTs
3. If NFTs > 0 → wait 2 min for sales to settle → recheck
4. If NFTs = 0 → wait 2 min anyway for funds to settle
5. Navigate to `https://rarbtc.com/person/myIncome`
6. Find `div.info` containing `div.text` with "personal reservation income"
7. Read its sibling `div.num` value (e.g. `$4.4728`) → this is the day income

### Session handling (throughout)
- Before every major step: check if URL contains `/login`
- If yes → re-login → close promotional popup → continue

### Email + log report — after all accounts done
- Write `account_N_report_YYYYMMDD.log` per account
- Send single SendGrid email with all accounts summarised

---

## Confirmed Site Selectors

All selectors confirmed from live HTML inspection. Critical for recreation.

### Login page (`/login`)
| Element | Selector |
|---|---|
| Cookie consent dismiss | `button.accept-btn` |
| Email login tab | `#tab-0` |
| Email input | `input[placeholder='Please enter your email']` |
| Password input | `input[placeholder='Password must be 8-20 characters or more']` |
| Login button | `div.bt.flex-center` |
| Login success detection | `page.wait_for_url(lambda url: "/login" not in url, timeout=20_000)` |

### Promotional popup (appears on every login and some pages)
| Element | Selector |
|---|---|
| Close button | `div.notice-btn div:last-child` |
| Structure | `div.notice-btn > div[Previous] + div[Close]` |

### Reservation page (`/nft/reservation`)
| Element | Selector |
|---|---|
| Reservation button | `button.one-bt` |
| Fund password PIN visual | `div.van-password-input` (click to focus) |
| Fund password hidden input | `div.pw input[type='text']` (fill this) |
| Confirm button | `button.van-button--primary` |
| Reservation Successful popup | `text='Reservation Successful'` |
| Sell NFT in success popup | `div.but button:last-child` |
| Tutorial overlay skip | `text='Skip'` |
| Reservations count | `li` containing `"Number of reservations available today"` → child `div.val` |

### My NFT page (`/nft/my`)
| Element | Selector |
|---|---|
| Sell NFT button | `button[data-v-5055aed9]` |
| NFT Sale popup | `text='NFT Sale'` |
| Confirm sell in popup | `button.van-button--primary` |
| Sale success text | `text='Selling application submitted successfully'` |
| I understand button | `button.van-button--primary` |

### Income page (`/person/myIncome`)
| Element | Selector |
|---|---|
| Income container | `div.info` (find one containing "personal reservation income") |
| Label | `div.info > div.text` |
| Value | `div.info > div.num` |
| Target label text | `"Today's personal reservation income"` |

---

## Critical Behaviours — Must Know for Recreation

1. **Login two-step redirect** — site goes `/login` → pause → `/home`. Use `page.wait_for_url()` not `time.sleep()`.

2. **Promotional popup on every page** — must dismiss with `div.notice-btn div:last-child` before any interaction on any page.

3. **Fund password is a PIN field** — not `input[type='password']`. It's a hidden `input[type='text']` inside `div.pw`. Must click `div.van-password-input` first to focus it before filling.

4. **Reservation Successful popup** — appears on `/nft/reservation` after up to 3 minutes. Has `div.but` with two buttons: `View NFT` (first) and `Sell NFT` (last-child).

5. **After clicking Sell NFT in reservation popup** — site may redirect to `/nft/reservation/list?id=X` (collection page), NOT `/nft/my`. Always force-navigate to `/nft/my` regardless.

6. **I understand button auto-closes** — success popup may dismiss before click. Always wrap in `try/except` — treat either outcome as success.

7. **Session expiry mid-run** — platform logs bot out after inactivity. Check URL before every step. Re-login + close popup if on `/login`.

8. **NFT total check** — use sell button count (`button[data-v-5055aed9]`) as the most reliable indicator. DOM text parsing is fragile.

9. **Ubuntu 24.04 runner** — `playwright install-deps` fails due to `libasound2` renamed to `libasound2t64`. Must install system dependencies manually in workflow.

10. **Cycle count is dynamic** — driven by `get_reservations_available()` at login. Some accounts allow 2, others 3. Never hardcode cycle count.

11. **`button.van-button--primary` is reused** — same class for: Confirm (fund password popup), Sell NFT (sale popup), I understand (success popup). Which action it performs depends on which popup is currently visible.

12. **Income reading** — do NOT calculate balance difference. Read directly from `/person/myIncome` → `div.info > div.num` where label contains "personal reservation income". Read after 2-minute settlement wait.

13. **`button.one-bt` disappears** — after reservations are used up, the Reservation button is replaced by "Appointment Countdown". Always check reservation count before clicking.

---

## Bot Architecture

```
main()
├── validate_env()                              # Check ACCOUNT_COUNT >= 1
├── Check email config → log if not set
└── loop account_num 1 to ACCOUNT_COUNT
    └── run_account(account_num)
        ├── get_account_credentials(N)          # Load RARBTC_USERNAME_N etc.
        │   └── None if missing → skip + log
        ├── Setup per-account logger            # account_N_bot_TIMESTAMP.log
        ├── Launch Playwright browser
        ├── RarbtcBot(page, username, password, reservation_password, account_num)
        │   ├── self.log = account logger
        │   ├── login()
        │   ├── get_reservations_available()    # DOM: li > div.val, "Number of reservations available today"
        │   ├── get_nfts_available()            # Count button[data-v-5055aed9]
        │   ├── _get_nft_total_number()         # Count sell buttons
        │   ├── ensure_no_nfts_before_reserve() # Sell existing → wait 2min if any
        │   ├── ensure_logged_in()              # Re-login if URL contains /login
        │   ├── get_today_reservation_income()  # /person/myIncome → div.info > div.num
        │   ├── reserve_nft()                   # Full reservation flow
        │   ├── sell_from_popup()               # Force nav to /nft/my → sell
        │   ├── sell_from_my_nfts()             # Find all sell buttons → sell each
        │   └── run_cycle(cycle_num, total)     # One buy-sell cycle
        ├── Collect opening stats
        ├── Loop: total_cycles = get_reservations_available() (cap 10)
        │   ├── Re-check reservations → break if 0
        │   └── run_cycle(N, total)
        ├── Sell any leftover NFTs
        ├── Wait 2min for settlement
        ├── get_today_reservation_income()
        ├── Write account_N_report_YYYYMMDD.log
        └── Return summary dict

send_run_notification(all_summaries)            # SendGrid email
```

### `run_cycle()` flow:
```
ensure_logged_in()
get_nfts_available() → if > 0 → sell_from_my_nfts() → sleep(10)
ensure_logged_in()
reserve_nft()
ensure_logged_in()
sell_from_popup()
```

### `reserve_nft()` flow:
```
goto /nft/reservation → close popup → dismiss tutorial
ensure_no_nfts_before_reserve()
  └── goto /nft/my → count sell buttons
      ├── > 0 → sell_from_my_nfts() → sleep(120)
      └── = 0 → continue
goto /nft/reservation → close popup
click button.one-bt
wait for div.pw input[type='text']
click div.van-password-input → fill hidden input → click button.van-button--primary
wait text='Reservation Successful' (180s timeout)
click div.but button:last-child (Sell NFT)
```

### `sell_from_popup()` flow:
```
goto /nft/my (force — ignore site redirect)
close popup if present
find button[data-v-5055aed9]
  ├── not found → log warning → return
  └── found → click → wait text='NFT Sale' (20s)
      ├── timeout → log warning → return
      └── appeared → click button.van-button--primary
          → wait text='Selling application submitted successfully'
          → try click button.van-button--primary (I understand)
          → sleep(10)
sleep(120)  ← 2 min between cycles
```

---

## GitHub Actions Setup

### Variables (Settings → Variables → Actions)
| Variable | Value |
|---|---|
| `ACCOUNT_COUNT` | Number of accounts to run (e.g. `2`) |

### Secrets (Settings → Secrets and variables → Actions)
For each account N (1 through ACCOUNT_COUNT):
| Secret | Value |
|---|---|
| `RARBTC_USERNAME_N` | Login email |
| `RARBTC_PASSWORD_N` | Login password |
| `RARBTC_RESERVATION_PASSWORD_N` | Fund/PIN password |

Email notification secrets (optional — bot continues if not set):
| Secret | Value |
|---|---|
| `SENDGRID_API_KEY` | SendGrid API key |
| `NOTIFY_EMAIL_TO` | Recipient email |
| `NOTIFY_EMAIL_FROM` | Verified sender email |

### Workflow key settings
```yaml
- cron: "23 3 * * *"        # 03:23 UTC = 05:23 SAST
timeout-minutes: 120         # Covers up to ~5 accounts
FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true
```

### System dependencies (Ubuntu 24.04 — do NOT use playwright install-deps)
```yaml
sudo apt-get install -y libasound2t64 libatk-bridge2.0-0 libatk1.0-0 
  libcups2 libdbus-1-3 libdrm2 libgbm1 libgtk-3-0 libnspr4 libnss3
  libx11-xcb1 libxcomposite1 libxdamage1 libxfixes3 libxkbcommon0 
  libxrandr2 xvfb
playwright install chromium   # browser only, no install-deps
```

---

## Email Report (SendGrid)

Sent after all accounts complete. Per account:
- Reservations at login
- NFTs available at login
- Reservations remaining after run
- NFTs unsold after run
- **Day income** (from `/person/myIncome`, not balance math)
- Human-friendly failure reason if failed/skipped

No totals across accounts in the email.

### Daily report log file
Written to `logs/account_N_report_YYYYMMDD.log`:
```
==================================================
  ACCOUNT 1 — Daily Report
  Date: 2026-05-27
  Status: SUCCESS
==================================================
  Reservations at login:          2
  NFTs available at login:        0
  Reservations after run:         0
  NFTs unsold after run:          0
  Day income:                     $4.4728
==================================================
```

---

## Timing Reference

| Step | Duration |
|---|---|
| Login + popup | ~30s |
| Opening stats collection | ~30s |
| Per reservation wait (order processing) | up to 3 min |
| Sell flow | ~1 min |
| Between cycles | 2 min |
| Settlement wait + income read | ~3 min |
| **Per account (2 reservations)** | ~18–22 min |
| **Per account (3 reservations)** | ~25–30 min |
| **Two accounts** | ~40–50 min |

GitHub free tier: 2,000 min/month. Two accounts daily ≈ 1,500 min/month — within free limits.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Secrets missing despite being set | Old workflow with `RARBTC_USERNAME` (no `_1`) | Replace with new single-step workflow |
| Bot ran twice in one log | Old dual-step workflow still in repo | Push new `nft_bot.yml` |
| `ACCOUNT_COUNT` not read | Variable not set in GitHub Actions Variables | Settings → Variables → Actions → add `ACCOUNT_COUNT` |
| Login fails | Wrong credentials or cookie popup not dismissed | Check secrets; `button.accept-btn` must exist |
| `button.one-bt` not found | Reservations used up or countdown showing | Normal — check reservation count first |
| Fund password not filled | PIN field not focused first | Click `div.van-password-input` before fill |
| Sell popup `text='NFT Sale'` timeout | Site redirected to collection page | `sell_from_popup` force-navigates to `/nft/my` |
| Income shows $0 or wrong | Balance math used instead of income page | Read from `/person/myIncome` → `div.info > div.num` |
| Ubuntu `libasound2` error | Renamed in Ubuntu 24 | Use `libasound2t64`; never use `playwright install-deps` |
| Session expired mid-run | Platform timeout | `ensure_logged_in()` checks URL and re-logins automatically |

---

## Sharing & Forking

### Make repo public → others fork it
- Credentials are never in code — safe to make public
- Fork users add their own secrets and `ACCOUNT_COUNT` variable
- Fork users get updates by clicking **Sync fork → Update branch** on GitHub

### Keep repo private → add collaborators
- Settings → Collaborators → add by GitHub username
- Collaborators can trigger runs and view logs but cannot see Secrets

---

## Local Development

```bash
git clone https://github.com/YOUR_USERNAME/rarbtc-nft-bot.git
cd rarbtc-nft-bot

python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# Fill in: RARBTC_USERNAME_1, RARBTC_PASSWORD_1, RARBTC_RESERVATION_PASSWORD_1
# Also set: ACCOUNT_COUNT=1

python bot.py
```

Logs written to `logs/` folder locally.

---

## Security

- All credentials stored as GitHub Secrets — encrypted, never in any committed file
- `.env` is in `.gitignore` — never committed
- Screenshots never capture credential fields (fields are filled, not visible in headless mode)
- See `SECURITY.md` for full policy
