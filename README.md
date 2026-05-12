# rarbtc.com NFT Trading Bot

Fully automated, cloud-hosted NFT buy-and-sell bot for [rarbtc.com](https://rarbtc.com).  
Runs daily at **08:00 UTC** via **GitHub Actions** — no local machine required.

---

## What it does

1. Logs into rarbtc.com using your credentials (stored as GitHub Secrets).
2. Navigates to `/nft/reservation` and clicks **Reserve**.
3. Enters the reservation password in the popup.
4. Waits up to **3 minutes** for the order confirmation popup.
5. Clicks **Sell NFT** and agrees to the offered sale value.
6. Waits 2 minutes for the sale to process.
7. Navigates to `/nft/my` and sells any listed NFTs.
8. Repeats steps 2–7 a **second time** (platform limit: 2 cycles per 24 h).
9. Logs every action, timestamp, success, and error to a log file.

If any step fails after 3 retries, the run aborts and saves an error screenshot.  
All logs and screenshots are available as downloadable GitHub Actions artifacts.

---

## Security — your credentials are always safe

| Where | How stored |
|---|---|
| Local development | `.env` file — listed in `.gitignore`, never committed |
| GitHub Actions | GitHub **Secrets** — encrypted, never visible in logs |
| Bot script | Read from environment variables at runtime only |

> **The `.env` file is never committed to GitHub. Your passwords never appear in any public place.**

---

## Setup — step by step

### Step 1 — Fork / create the repository

1. Go to [github.com](https://github.com) → **New repository**.
2. Name it (e.g. `rarbtc-nft-bot`), set it to **Private** (recommended).
3. Upload all files from this project into it, preserving the folder structure:

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

> ⚠️ Do **not** upload your `.env` file — only upload `.env.example`.

---

### Step 2 — Add your credentials as GitHub Secrets

1. In your repo, go to **Settings → Secrets and variables → Actions**.
2. Click **New repository secret** for each of the three secrets below:

| Secret name | Value |
|---|---|
| `RARBTC_USERNAME` | Your rarbtc.com username or email |
| `RARBTC_PASSWORD` | Your rarbtc.com password |
| `RARBTC_RESERVATION_PASSWORD` | Your reservation popup password |

---

### Step 3 — Verify the workflow is active

1. Go to the **Actions** tab in your repo.
2. You should see **"NFT Bot Daily Run"** listed.
3. To test it immediately: click the workflow → **Run workflow** → **Run workflow**.

The bot will install all dependencies automatically and run in the cloud.

---

### Step 4 — View logs after a run

1. Go to **Actions** → click a completed run.
2. Scroll to **Artifacts** at the bottom.
3. Download **`bot-logs-<run-id>`** to see the full timestamped log.
4. If the run failed, an **`error-screenshot-<run-id>`** artifact will also appear.

---

## Local development (optional)

If you want to test locally before pushing to GitHub:

```bash
# 1. Clone your repo
git clone https://github.com/YOUR_USERNAME/rarbtc-nft-bot.git
cd rarbtc-nft-bot

# 2. Create a virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium

# 4. Set up your credentials
cp .env.example .env
# Open .env and fill in your real values

# 5. Run the bot
python bot.py
```

Logs will appear in the `logs/` folder.

---

## Schedule

The bot runs automatically every day at **08:00 UTC**.  
To change the time, edit `.github/workflows/nft_bot.yml`:

```yaml
on:
  schedule:
    - cron: "0 8 * * *"   # ← change this (minute hour * * *)
```

[Cron expression reference](https://crontab.guru/)

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Login fails | Wrong credentials secret | Re-check GitHub Secrets spelling and values |
| Reserve button not found | Site layout changed | Check error screenshot; update selector in `bot.py` |
| Popup never appears | Slow server | Timeout is 3 min; if consistent, check your account balance/eligibility |
| `playwright install` fails | OS dep issue | The workflow handles this automatically on GitHub Actions |

---

## Files overview

| File | Purpose |
|---|---|
| `bot.py` | Main automation script |
| `requirements.txt` | Python dependencies |
| `.env.example` | Credential template (safe to commit) |
| `.gitignore` | Prevents `.env` and logs from being committed |
| `.github/workflows/nft_bot.yml` | GitHub Actions schedule and runner config |

---

> **Disclaimer:** This bot interacts with rarbtc.com on your behalf. Ensure your use complies with the platform's terms of service.
