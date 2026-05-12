# Rarzz NFT Trading Bot

Fully automated, cloud-hosted NFT buy-and-sell bot for [rarbtc.com](https://rarbtc.com) (Rarzz NFT platform).  
Runs daily at **07:00 SAST (05:00 UTC)** via **GitHub Actions** — no local machine required.  
Supports **two accounts** running sequentially in one daily job.

---

## Project Summary

This bot was built iteratively through debugging on the live site. Every selector, popup, and flow was discovered by running the bot, capturing error screenshots and HTML dumps, and updating the code to match the real site structure.

---

## What the Bot Does

### On every daily run:
1. Logs into rarbtc.com (handles cookie popup, email login tab, promotional popup)
2. Checks **number of reservations available today**
3. Runs up to **2 cycles** of reserve → sell per account

### Decision logic per cycle:

| Reservations Available | Action |
|---|---|
| 2 | Check NFT total → sell existing → Reserve → Sell → wait 5 min |
| 1 | Check NFT total → sell existing → Reserve → Sell → wait 5 min |
| 0 | Go to `/nft/my` → sell any available NFTs → done |

### Before every reservation:
- Navigate to `/nft/my`
- Check **NFT total number** via DOM (`button[data-v-5055aed9]` sell button count)
- If **> 0** → sell all existing NFTs → wait 5 min → then reserve
- If **= 0** → reserve immediately

### Full sell flow:
1. Click `Sell NFT` button on My NFT page (`button[data-v-5055aed9]`)
2. NFT Sale popup appears → click `button.van-button--primary` (Sell NFT)
3. Wait for `"Selling application submitted successfully"`
4. Click `button.van-button--primary` (I understand) — handles auto-close gracefully
5. Wait **5 minutes** before next cycle

### Session handling:
- If session expires mid-run → auto re-login → close promotional popup → continue

---

## Site Structure & Confirmed Selectors

These selectors were confirmed from live HTML inspection during development:

### Login page (`/login`)
| Element | Selector |
|---|---|
| Cookie consent dismiss | `button.accept-btn` |
| Email login tab | `#tab-0` |
| Email input | `input[placeholder='Please enter your email']` |
| Password input | `input[placeholder='Password must be 8-20 characters or more']` |
| Login button | `div.bt.flex-center` |
| Login success check | Wait for URL to leave `/login` via `page.wait_for_url()` |

### Promotional popup (appears on every login and page)
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
| Reservations count container | `li` containing `"Number of reservations available today"` → `div.val` |

### My NFT page (`/nft/my`)
| Element | Selector |
|---|---|
| Sell NFT button | `button[data-v-5055aed9]` |
| NFT Sale popup | `text='NFT Sale'` |
| Confirm sell in popup | `button.van-button--primary` |
| Success text | `text='Selling application submitted successfully'` |
| I understand button | `button.van-button--primary` |

### Tutorial overlay (appears on first visit to reservation page)
| Element | Selector |
|---|---|
| Skip button | `text='Skip'` |

---

## Key Behaviours Discovered During Development

1. **Login redirect** — site does a two-step redirect: `/login` → brief pause → `/home`. Must use `page.wait_for_url()` not `time.sleep()`.
2. **Promotional popup** — appears on every login and on some page navigations. Must be dismissed before any interaction.
3. **Fund password field** — not a standard `input[type='password']`. It's a PIN-style field: hidden `input[type='text']` with `maxlength` inside `div.pw`. Click `div.van-password-input` first to focus.
4. **Reservation Successful popup** — appears on `/nft/reservation` after order processes. Has `div.but` with two buttons: `View NFT` and `Sell NFT` (last-child).
5. **After clicking Sell NFT in reservation popup** — navigate to `/nft/my`. The NFT Sale popup does NOT appear automatically on the reservation page.
6. **I understand button** — may auto-close before the bot clicks it. Handle with `try/except` — treat either outcome as success.
7. **Session expiry** — site can log the bot out mid-run. Bot checks URL before each step and re-logs in if on `/login`.
8. **NFT total check** — use sell button count (`button[data-v-5055aed9]`) as the most reliable indicator of sellable NFTs.
9. **Ubuntu 24.04 compatibility** — `playwright install-deps` fails due to `libasound2` being renamed to `libasound2t64`. Must install system deps manually.

---

## Timing

| Step | Duration |
|---|---|
| Login + popup close | ~30s |
| Check reservations | ~20s |
| NFT total check | ~15s |
| Reserve NFT (wait for order) | ~1-3 min |
| Sell flow | ~1 min |
| Wait between cycles | 5 min |
| **Per account total** | ~15-25 min |
| **Two accounts total** | ~30-50 min |

---

## GitHub Actions Setup

### Secrets required (Settings → Secrets and variables → Actions):

| Secret | Description |
|---|---|
| `RARBTC_USERNAME_1` | Account 1 login email |
| `RARBTC_PASSWORD_1` | Account 1 login password |
| `RARBTC_RESERVATION_PASSWORD_1` | Account 1 fund/PIN password |
| `RARBTC_USERNAME_2` | Account 2 login email |
| `RARBTC_PASSWORD_2` | Account 2 login password |
| `RARBTC_RESERVATION_PASSWORD_2` | Account 2 fund/PIN password |

### Workflow features:
- Runs daily at **05:00 UTC (07:00 SAST)**
- `continue-on-error: true` on both account steps — Account 2 always runs even if Account 1 fails
- `timeout-minutes: 90` to cover both accounts
- Logs uploaded as artifacts after every run (pass or fail)
- Error screenshots and HTML page dumps saved for debugging

---

## Repository Structure

```
rarbtc-nft-bot/
├── .github/
│   └── workflows/
│       └── nft_bot.yml       # GitHub Actions schedule and runner
├── bot.py                    # Main automation script
├── requirements.txt          # playwright==1.44.0, python-dotenv==1.0.1
├── .env.example              # Credential template (safe to commit)
├── .gitignore                # Blocks .env, logs, screenshots
└── README.md                 # This file
```

---

## Local Development

```bash
# Clone repo
git clone https://github.com/YOUR_USERNAME/rarbtc-nft-bot.git
cd rarbtc-nft-bot

# Setup
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

# Credentials
cp .env.example .env
# Fill in your values in .env

# Run
python bot.py
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Login fails | Wrong credentials | Re-check GitHub Secrets |
| `button.one-bt` not found | 0 reservations left today | Normal — bot skips to sell check |
| Fund password popup not found | Reservation button click failed | Check for overlay blocking click |
| Confirmation popup timeout | Server slow / order failed | Bot saves HTML — inspect selectors |
| Session expired mid-run | Platform timeout | Bot auto re-logins |
| `libasound2` install error | Ubuntu 24.04 renamed package | Already fixed in workflow — installs `libasound2t64` |
| AttributeError on method | Stale bot.py in repo | Force push latest: `git push --force` |

---

## Security

- Credentials stored as **GitHub Secrets** — encrypted, never visible in logs or code
- `.env` file is in `.gitignore` — never committed
- Bot reads credentials from environment variables at runtime only
- Screenshots never capture credential fields

---

## Bot Architecture (for developers)

```
main()
├── validate_env()
├── launch_browser()
└── RarbtcBot(page)
    ├── login()                          # Login + close popup
    ├── get_reservations_available()     # DOM check on /nft/reservation
    ├── get_nfts_available()             # Sell button count on /nft/my
    ├── _get_nft_total_number()          # DOM sell button count
    ├── ensure_no_nfts_before_reserve()  # Sell existing NFTs if any before reserving
    ├── ensure_logged_in()               # Re-login if session expired
    ├── reserve_nft()                    # Full reservation flow
    ├── sell_from_popup()                # Sell after reservation success popup
    ├── sell_from_my_nfts()             # Sell any NFTs on /nft/my
    └── run_cycle(cycle_num)            # Orchestrates one full buy-sell cycle
```

### `run_cycle()` decision tree:
```
Check reservations available
├── 0 → Check /nft/my → sell if any → done
├── 1 → Sell existing NFTs first → reserve → sell → 5min wait
└── 2 → Sell existing NFTs first → reserve → sell → 5min wait
         (cycle 2 repeats same logic)
```

### `reserve_nft()` flow:
```
ensure_no_nfts_before_reserve()
  └── /nft/my sell button count
      ├── > 0 → sell_from_my_nfts() → wait 5min
      └── = 0 → continue
Navigate to /nft/reservation
Close popup if present
Click button.one-bt (Reservation)
Wait for div.pw input[type='text'] (Fund password)
Fill password → Click button.van-button--primary (Confirm)
Wait for text='Reservation Successful' (up to 3 min)
Click div.but button:last-child (Sell NFT)
```

### `sell_from_popup()` flow:
```
Navigate to /nft/my
Close popup if present
Check for text='NFT Sale' (auto-shown, 10s timeout)
├── Not shown → find button[data-v-5055aed9] → click
└── Shown → proceed
Click button.van-button--primary (Sell NFT in popup)
Wait for text='Selling application submitted successfully'
Click button.van-button--primary (I understand) — graceful if auto-closed
Wait 5 minutes
```
