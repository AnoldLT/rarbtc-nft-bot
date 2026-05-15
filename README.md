# Rarzz NFT Trading Bot

Fully automated, cloud-hosted NFT buy-and-sell bot for [rarbtc.com](https://rarbtc.com) (Rarzz NFT platform).
Runs daily at **07:00 SAST (05:00 UTC)** via **GitHub Actions** — no local machine required.
Supports **N accounts** running sequentially in one daily job.

---

## Project Summary & Developer Brief

This bot was built iteratively through live debugging on rarbtc.com. Every selector, popup, and flow was discovered by running the bot, capturing error screenshots and HTML dumps, and updating the code to match the real site structure. A new developer can use this document alone to understand, recreate, or extend the bot.

---

## Tech Stack

- **Language**: Python 3.11
- **Browser automation**: Playwright (headless Chromium)
- **Scheduling**: GitHub Actions (cron)
- **Credential storage**: GitHub Secrets + GitHub Actions Variables
- **Dependencies**: `playwright==1.44.0`, `python-dotenv==1.0.1`

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
├── SECURITY.md                # Security policy and credential safety guide
└── README.md                  # This file
```

---

## What the Bot Does

### On every daily run:
1. Reads `ACCOUNT_COUNT` variable to know how many accounts to process
2. For each account (1 through N):
   - Logs in to rarbtc.com
   - Checks reservations available today
   - Runs up to 2 buy-sell cycles
   - Produces a separate log file
   - Continues to next account regardless of success or failure

### Decision logic per cycle:

| Reservations Available | Action |
|---|---|
| 2 | Check NFT total → sell existing → Reserve → Sell → wait 5 min |
| 1 | Check NFT total → sell existing → Reserve → Sell → wait 5 min |
| 0 | Go to `/nft/my` → sell any available NFTs → done |

### Before every reservation:
- Navigate to `/nft/my`
- Count sell buttons (`button[data-v-5055aed9]`) as NFT total indicator
- If **> 0** → sell all existing NFTs → wait 5 min → then reserve
- If **= 0** → reserve immediately

### Full sell flow (per NFT):
1. Click `Sell NFT` (`button[data-v-5055aed9]`)
2. NFT Sale popup → click `button.van-button--primary`
3. Wait for `"Selling application submitted successfully"`
4. Click `button.van-button--primary` (I understand) — handles auto-close gracefully
5. Wait **5 minutes** before next cycle

### Session handling:
- Checks URL before every major step
- If redirected to `/login` → auto re-login → close promotional popup → continue

---

## Site Structure & Confirmed Selectors

All selectors confirmed from live HTML inspection during development.

### Login page (`/login`)

| Element | Selector / Method |
|---|---|
| Cookie consent dismiss | `button.accept-btn` |
| Email login tab | `#tab-0` |
| Email input | `input[placeholder='Please enter your email']` |
| Password input | `input[placeholder='Password must be 8-20 characters or more']` |
| Login button | `div.bt.flex-center` |
| Login success | `page.wait_for_url(lambda url: "/login" not in url, timeout=20_000)` |

### Promotional popup (appears on every login and some page navigations)

| Element | Selector |
|---|---|
| Close button | `div.notice-btn div:last-child` |

### Reservation page (`/nft/reservation`)

| Element | Selector |
|---|---|
| Reservation button | `button.one-bt` |
| Fund password PIN input | `div.pw input[type='text']` |
| PIN visual display (click to focus) | `div.van-password-input` |
| Confirm button | `button.van-button--primary` |
| Reservation Successful popup | `text='Reservation Successful'` |
| Sell NFT in success popup | `div.but button:last-child` |
| Reservations count | `li` containing `"Number of reservations available today"` → child `div.val` |
| Tutorial overlay skip | `text='Skip'` |

### My NFT page (`/nft/my`)

| Element | Selector |
|---|---|
| Sell NFT button | `button[data-v-5055aed9]` |
| NFT Sale popup | `text='NFT Sale'` |
| Confirm sell | `button.van-button--primary` |
| Success confirmation | `text='Selling application submitted successfully'` |
| I understand button | `button.van-button--primary` |

---

## Key Behaviours Discovered During Development

These were all found through failed runs and HTML inspection — critical for any developer recreating this:

1. **Login two-step redirect** — site goes `/login` → brief pause → `/home`. Must use `page.wait_for_url()` not `time.sleep()` to detect success.

2. **Promotional popup** — appears on every login and on some page navigations. Must always be dismissed before any interaction. Selector: `div.notice-btn div:last-child`.

3. **Fund password field is not a standard password input** — it's a PIN-style field: hidden `input[type='text']` with `maxlength` inside `div.pw`. Must click `div.van-password-input` first to focus, then fill the hidden input.

4. **Reservation Successful popup** — appears on `/nft/reservation` after order processes (up to 3 min wait). Contains `div.but` with two buttons: `View NFT` (first) and `Sell NFT` (last-child).

5. **After clicking Sell NFT in reservation popup** — must navigate to `/nft/my`. The NFT Sale popup does NOT appear automatically on the reservation page.

6. **I understand button auto-closes** — the success popup may dismiss itself before the bot clicks the button. Always wrap in `try/except` and treat either outcome as success.

7. **Session expiry mid-run** — the platform can log the bot out after inactivity. Bot checks URL before each step and re-logs in automatically if on `/login`.

8. **NFT total check** — use sell button count (`button[data-v-5055aed9]`) as the most reliable indicator of sellable NFTs. DOM text parsing is fragile due to layout.

9. **Ubuntu 24.04 runner incompatibility** — `playwright install-deps` fails because `libasound2` was renamed to `libasound2t64` in Ubuntu 24. Must install system dependencies manually in the workflow.

10. **`button.one-bt` disappears after reservations are used** — replaced by an "Appointment Countdown" element. Always check reservation count before attempting to click.

11. **`button.van-button--primary` is used for multiple popups** — Confirm (fund password), Sell NFT (sale popup), and I understand (success popup) all use the same class. Context (current visible popup) determines which action it performs.

---

## Bot Architecture

```
main()
├── validate_env()                          # Check ACCOUNT_COUNT >= 1
└── loop account_num 1 to ACCOUNT_COUNT
    └── run_account(account_num)
        ├── get_account_credentials(N)      # Load RARBTC_USERNAME_N etc.
        │   └── Returns None if missing → skip with log
        ├── Setup account-specific logger   # account_N_bot_TIMESTAMP.log
        ├── Launch Playwright browser
        └── RarbtcBot(page, username, password, reservation_password, account_num)
            ├── login()
            ├── get_reservations_available()
            ├── get_nfts_available()
            ├── _get_nft_total_number()
            ├── ensure_no_nfts_before_reserve()
            ├── ensure_logged_in()
            ├── reserve_nft()
            ├── sell_from_popup()
            ├── sell_from_my_nfts()
            └── run_cycle(N)
```

### `run_cycle()` decision tree:
```
Check reservations available (DOM on /nft/reservation)
├── 0 → Check /nft/my → sell if any NFTs → done
├── 1 → Sell existing NFTs first → reserve → sell → wait 5 min
└── 2 → Sell existing NFTs first → reserve → sell → wait 5 min
         (cycle 2 repeats same logic)
```

### `reserve_nft()` flow:
```
ensure_no_nfts_before_reserve()
  └── Count button[data-v-5055aed9] on /nft/my
      ├── > 0 → sell_from_my_nfts() → wait 5 min
      └── = 0 → continue
Navigate to /nft/reservation
Close popup if present
Click button.one-bt (Reservation button)
Wait for div.pw input[type='text'] (Fund password popup)
Click div.van-password-input to focus
Fill hidden input with reservation_password
Click button.van-button--primary (Confirm)
Wait for text='Reservation Successful' (up to 3 min)
Click div.but button:last-child (Sell NFT)
```

### `sell_from_popup()` flow:
```
Navigate to /nft/my
Close popup if present
Wait 10s for text='NFT Sale' (auto-shown)
├── Not shown → click button[data-v-5055aed9] manually → wait for NFT Sale popup
└── Shown → proceed
Click button.van-button--primary (Sell NFT in popup)
Wait for text='Selling application submitted successfully'
Try click button.van-button--primary (I understand) — graceful if auto-closed
Wait 5 minutes
```

---

## Multi-Account Setup

### Step 1 — Set ACCOUNT_COUNT variable
GitHub repo → **Settings → Variables → Actions → New repository variable**:

| Variable | Value |
|---|---|
| `ACCOUNT_COUNT` | Number of accounts (e.g. `1`, `2`, `5`) |

### Step 2 — Add secrets for each account
GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**:

For each account `N` from 1 to ACCOUNT_COUNT:

| Secret | Value |
|---|---|
| `RARBTC_USERNAME_N` | Account N login email |
| `RARBTC_PASSWORD_N` | Account N login password |
| `RARBTC_RESERVATION_PASSWORD_N` | Account N fund/PIN password |

The workflow pre-loads secrets for up to 5 accounts. To support more, add additional secret references in `nft_bot.yml`.

### Account skip behaviour
If secrets for an account are missing, the bot:
- Logs the skip reason to `account_N_SKIPPED_TIMESTAMP.log`
- Continues to the next account without failing the run

### Log files per run
```
logs/
├── account_1_bot_20260513_050012.log
├── account_2_bot_20260513_051800.log
├── account_3_SKIPPED_20260513_053000.log
├── account_1_error_20260513_050512.png    (if account 1 failed)
└── account_1_error_page_20260513_050512.html
```

### Timing estimate

| Accounts | Est. daily runtime |
|---|---|
| 1 | ~20 min |
| 2 | ~40 min |
| 3 | ~60 min |
| 5 | ~100 min |

GitHub free tier: 2,000 min/month. 5 accounts × 30 days ≈ 3,000 min — consider a paid plan or self-hosted runner for 5+ accounts.

---

## Schedule

Runs automatically every day at **07:00 SAST / 05:00 UTC**.

To change, edit `.github/workflows/nft_bot.yml`:
```yaml
- cron: "0 5 * * *"   # minute hour * * *
```
[Cron expression helper](https://crontab.guru/)

**Note:** GitHub Actions scheduled runs can be delayed by up to 15–30 minutes during busy periods. This is normal. If the bot hasn't run by 07:30 SAST, trigger it manually via Actions → Run workflow.

**Important:** GitHub automatically **disables scheduled workflows** on repos that have had no activity (commits, pushes) for **60 days**. To keep the schedule active, either make occasional commits or trigger manual runs.

---

## GitHub Actions Workflow Notes

- `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true` — required to suppress Node.js 20 deprecation warnings on current runners
- `timeout-minutes: 120` — covers up to ~5 accounts with buffer
- System dependencies installed manually due to Ubuntu 24.04 renaming `libasound2` → `libasound2t64`
- All logs uploaded as artifacts after every run (pass or fail), retained 30 days

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
# Fill in your values — use _1 suffix: RARBTC_USERNAME_1 etc.

python bot.py
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Scheduled run didn't fire | GitHub delay or disabled workflow | Trigger manually; check Actions tab for disabled warning |
| Login fails | Wrong credentials | Re-check GitHub Secrets |
| `button.one-bt` not found | 0 reservations left today | Normal — bot skips to sell check |
| Fund password popup not found | Reservation click failed or overlay blocking | Check error screenshot |
| Confirmation popup timeout | Server slow / order failed | Bot saves HTML — check selectors |
| Session expired mid-run | Platform timeout | Bot auto re-logins |
| `libasound2` install error | Ubuntu 24.04 renamed package | Already fixed — installs `libasound2t64` |
| `AttributeError` on method | Stale bot.py in repo | Force push: `git push --force` |
| Account skipped | Missing GitHub Secrets | Add `RARBTC_USERNAME_N` etc. for that account |
| Bot ran but no trades | 0 reservations + 0 NFTs | Normal if already completed today |

---

## Security Summary

- Credentials stored as **GitHub Secrets** — encrypted, never in code or logs
- `.env` blocked by `.gitignore` — never committed
- Safe to make repo public — no credentials in any committed file
- Others can fork and add their own secrets independently
- See `SECURITY.md` for full security policy

---

## Sharing the Repo

### Option A — Others manage their own bot (recommended)
1. Make repo **Public**
2. Share the link
3. Others **Fork** it → add their own secrets → enable Actions

### Option B — You manage bot for others
1. Keep repo **Private**
2. Add them as **Collaborator** (Settings → Collaborators)
3. They can trigger runs and view logs but cannot see your Secrets

---

## Email Notifications (Daily Report)

After all accounts complete their daily run, the bot sends a single email report via **SendGrid**.

### What the email includes

Per account:
- Reservations available at login
- NFTs available to sell at login
- Account balance at login
- Reservations remaining after run
- NFTs unsold after run
- Account balance after run
- **Day income** (balance end − balance start)
- Human-friendly failure reason (if the account failed or was skipped)

Summary:
- **Total day income** across all accounts

### Setup

**Step 1 — Create a SendGrid account**
1. Go to [sendgrid.com](https://sendgrid.com) → sign up (free tier = 100 emails/day)
2. Settings → API Keys → Create API Key → Full Access
3. Copy the key — it only shows once
4. Settings → Sender Authentication → Single Sender Verification → verify your sending email address

**Step 2 — Add three GitHub Secrets**

Go to repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `SENDGRID_API_KEY` | Your SendGrid API key |
| `NOTIFY_EMAIL_TO` | Email address to receive the daily report |
| `NOTIFY_EMAIL_FROM` | Verified sender email (e.g. `rarbtcbot@gmail.com`) |

**Email notifications are optional.** If these secrets are not set, the bot logs a note and continues running normally — it will not fail.

### Email report format

```
Rarzz NFT Bot — Daily Report
2026-05-14 | 08:05 UTC

Account 1                               SUCCESS
─────────────────────────────────────────────
Reservations at login              2
NFTs available at login            0
Balance at login                   325.9100 USDT
Reservations remaining after run   0
NFTs unsold after run              0
Balance after run                  330.1500 USDT
Day income                         +4.2400 USDT

Account 2                               SUCCESS
─────────────────────────────────────────────
...

Total Day Income across all accounts:  +8.4800 USDT
```

If an account fails:
```
Account 3                               FAILED
─────────────────────────────────────────────
...
Issue: Reservation was placed but no confirmation appeared within 3 minutes.
```

If secrets are missing for an account:
```
Account 4                               SKIPPED
─────────────────────────────────────────────
Issue: Missing GitHub Secrets: RARBTC_USERNAME_4, RARBTC_PASSWORD_4
```
