# Rarzz NFT Trading Bot

Fully automated, cloud-hosted NFT buy-and-sell bot for [rarbtc.com](https://rarbtc.com) (Rarzz NFT platform).  
Runs daily at **07:00 SAST (05:00 UTC)** via **GitHub Actions** — no local machine required.

---

## What it does

The bot runs 2 cycles per day (platform limit). Each cycle is fully automated:

### On every run:
1. Logs into rarbtc.com (dismisses cookie popup, email login tab, promotional popup)
2. Checks **reservations available today** on `/nft/reservation`
3. Checks **amount available for reservation** before each reserve attempt
4. Runs up to **2 cycles** of reserve → sell

### Per cycle logic:

| Reservations Available | Action |
|---|---|
| 2 | Sell any existing NFTs first → Reserve → Sell → wait 10 min |
| 1 | Sell any existing NFTs first → Reserve → Sell → wait 10 min |
| 0 | Skip reservation → check `/nft/my` → sell any available NFTs |

### Balance check before every reservation:
- If **Amount available ≥ 250 USDT** → reserve immediately
- If **Amount available < 250 USDT** → go to `/nft/my` first
  - NFTs found → sell them → wait 10 min → re-check → reserve
  - No NFTs → wait 10 min → proceed to reserve anyway

### Sell flow:
1. Navigate to `/nft/my`
2. Click **Sell NFT** button
3. Confirm in the NFT Sale popup
4. Click **I understand** on the success popup
5. Wait **10 minutes** before next cycle

### Session handling:
- If session expires mid-run, bot automatically re-logs in and closes the promotional popup before continuing

### On every run the bot logs:
- All actions with timestamps
- Reservation count and amount available
- NFT counts found and sold
- Any errors with full traceback + screenshot + page HTML for debugging

---

## Security — your credentials are always safe

| Where | How stored |
|---|---|
| Local development | `.env` file — listed in `.gitignore`, never committed |
| GitHub Actions | GitHub **Secrets** — encrypted, never visible in logs |
| Bot script | Read from environment variables at runtime only |

> **Your passwords never appear in any file committed to GitHub.**

---

## Setup — step by step

### Step 1 — Create the repository

1. Go to [github.com](https://github.com) → **New repository**
2. Name it (e.g. `rarbtc-nft-bot`), set it to **Private** (recommended)
3. Push all files preserving this structure:

```
rarbtc-nft-bot/
├── .github/
│   └── workflows/
│       └── nft_bot.yml
├── bot.py
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

> Do **not** commit your `.env` file — only `.env.example`.

---

### Step 2 — Add credentials as GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret name | Value |
|---|---|
| `RARBTC_USERNAME` | Your rarbtc.com login email |
| `RARBTC_PASSWORD` | Your rarbtc.com login password |
| `RARBTC_RESERVATION_PASSWORD` | Your fund/reservation PIN password |

---

### Step 3 — Verify the workflow is active

1. Go to the **Actions** tab in your repo
2. You should see **"NFT Bot Daily Run"** listed
3. To test immediately: click the workflow → **Run workflow** → **Run workflow**

All dependencies (Python, Playwright, Chromium) install automatically on the GitHub runner.

---

### Step 4 — View logs after a run

1. Go to **Actions** → click a completed run
2. Scroll to **Artifacts** at the bottom
3. Download **`bot-logs-<run-id>`** — contains:
   - Full timestamped `.log` file
   - Error screenshot (`.png`) if the run failed
   - Page HTML dump (`.html`) for selector debugging if needed

---

## Schedule

Runs automatically every day at **07:00 SAST / 05:00 UTC**.

To change the time, edit `.github/workflows/nft_bot.yml`:

```yaml
on:
  schedule:
    - cron: "0 5 * * *"   # 05:00 UTC = 07:00 SAST
```

[Cron expression helper](https://crontab.guru/)

---

## Full daily run timeline (estimate)

| Step | Duration |
|---|---|
| Login + popup close | ~30s |
| Check reservations + balance | ~20s |
| Reserve NFT (per cycle) | ~3 min |
| Sell flow (per cycle) | ~1 min |
| 10 min wait between cycles | 10 min |
| Cycle 2 (same as above) | ~14 min |
| **Total** | **~35–45 min** |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Login fails | Wrong credentials | Re-check GitHub Secrets |
| Reservation button not found | Used all reservations today | Normal — bot skips to sell check |
| Confirmation popup timeout | Slow server / order processing | Bot saves page HTML — share with developer |
| Session expired mid-run | Platform timeout | Bot auto re-logs in |
| Amount available < 250 USDT | NFT still processing | Bot sells existing NFTs and waits 10 min |

---

## Files overview

| File | Purpose |
|---|---|
| `bot.py` | Main automation script |
| `requirements.txt` | Python dependencies (Playwright, python-dotenv) |
| `.env.example` | Credential template (safe to commit) |
| `.gitignore` | Prevents `.env` and logs from being committed |
| `.github/workflows/nft_bot.yml` | GitHub Actions daily schedule and runner config |

---

> **Disclaimer:** This bot interacts with rarbtc.com on your behalf. Ensure your use complies with the platform's terms of service.
