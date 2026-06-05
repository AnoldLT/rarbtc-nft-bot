# Security Policy

## Credential storage

All credentials (login email, password, fund/PIN password) are stored exclusively as **GitHub Secrets** when deployed via GitHub Actions. They are:

- Encrypted at rest by GitHub
- Never printed in workflow logs
- Never committed to the repository in any file
- Not visible to collaborators (only repository admins can manage secrets)

The `.env` file used for local development is listed in `.gitignore` and must never be committed.

## What is safe to commit / make public

- `bot.py` — contains no credentials; reads everything from environment variables
- `requirements.txt` — dependency list only
- `.env.example` — template with placeholder values only
- `.gitignore` — blocks `.env` and logs
- `.github/workflows/nft_bot.yml` — workflow config; credentials injected at runtime from Secrets
- `README.md`, `SECURITY.md` — documentation

## Local development

When running locally, create a `.env` file from `.env.example`. This file is git-ignored and must not be shared or pushed.

## Reporting a vulnerability

If you discover a security issue in this project, please open a GitHub Issue or contact the repository owner directly.
