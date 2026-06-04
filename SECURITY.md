# Security

Do not publish credentials or local runtime artifacts.

Never commit:

- `.env` files
- `python/race_secrets.json`
- `python/race_config.json` if it contains private endpoints or local settings
- `python/race_history.db`
- `python/sector_said_exports/*.json`
- API keys, tokens, passwords, cookies, or session files

If you find a security issue in this public export, please open a GitHub issue
with enough detail to reproduce the problem, but do not include working secrets
or private account data.
