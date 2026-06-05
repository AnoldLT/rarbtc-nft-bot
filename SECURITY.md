# Security Policy

## Credential Safety

This project is designed so that **no credentials ever appear in the codebase**.

All sensitive values are stored exclusively as **GitHub Secrets** or **GitHub Actions Variables**, which are:
- Encrypted at rest by GitHub
- Never visible in logs, code, or to collaborators
- Injected as environment variables only at runtime
- Automatically redacted if accidentally printed to logs

### What is safe to share or make public

| File | Safe to share | Reason |
|---|---|---|
| `bot.py` | ✅ Yes | Reads credentials from environment only |
| `nft_bot.yml` | ✅ Yes | References secret names only, not values |
| `README.md` | ✅ Yes | No credentials |
| `SECURITY.md` | ✅ Yes | No credentials |
| `.env.example` | ✅ Yes | Template only, no real values |
| `.env` | ❌ Never | Contains real credentials — blocked by `.gitignore` |
| `logs/` | ⚠️ Caution | May contain account activity — kept local only |

---

## For Repository Owners

### Before making your repo public
- Run `git log --all --full-history -- .env` — if it returns nothing, your `.env` was never committed
- Run `git grep -i "password\|secret\|token"` — ensure no hardcoded credentials exist
- Confirm `.gitignore` includes `.env`, `logs/`, and `*.png`

### GitHub Secrets management
- Secrets are set under **Settings → Secrets and variables → Actions**
- Never paste secret values into issues, pull requests, or commit messages
- Rotate credentials immediately if you suspect exposure
- Remove secrets for accounts that are no longer in use

### Collaborator access
- Collaborators can trigger workflow runs and view logs but **cannot read your Secrets**
- Only grant collaborator access to trusted individuals
- Review collaborators regularly under **Settings → Collaborators**

---

## For Users Forking This Repository

When you fork this project:
1. You get a clean copy with **no credentials** — the original owner's secrets do not transfer
2. You must add your own secrets under your fork's **Settings → Secrets and variables → Actions**
3. Set your own `ACCOUNT_COUNT` variable under **Settings → Variables → Actions**
4. Your credentials are entirely separate from the original repo

### Required secrets per account
For each account numbered `N`:
```
RARBTC_USERNAME_N
RARBTC_PASSWORD_N
RARBTC_RESERVATION_PASSWORD_N
```

---

## Reporting a Security Issue

If you discover a security vulnerability in this project — such as a code path that could expose credentials or allow unauthorized access — please:

1. **Do not open a public GitHub Issue**
2. Contact the repository owner directly and privately
3. Provide a clear description of the vulnerability and steps to reproduce
4. Allow reasonable time for the issue to be fixed before any public disclosure

---

## What This Bot Does Not Do

To be transparent about the bot's scope:

- Does **not** store, transmit, or log your credentials anywhere outside GitHub's encrypted secrets vault
- Does **not** make any transactions beyond what is described in the README (NFT reservation and sale on rarbtc.com)
- Does **not** access any data beyond what is needed to perform the automation
- Does **not** run any code from external sources at runtime

---

## Disclaimer

This bot automates actions on rarbtc.com on behalf of the account owner. Users are responsible for:
- Ensuring their use complies with rarbtc.com's terms of service
- Securing their own GitHub account (enable 2FA)
- Rotating credentials if they suspect compromise
- Reviewing the bot's actions via the daily log artifacts

The authors of this project take no responsibility for financial loss, account suspension, or any other consequences arising from the use of this automation.
