# Funnel Leak Hunt Cloud Setup

This repository configures read-only Meta Ads and Skool CLI access for challenge participants.

## Participant intent

When participant asks to set up, connect, check, or start Funnel Leak Hunt:

1. Read `.claude/skills/cloud-setup/SKILL.md` completely.
2. Follow it in order.
3. Explain failures in plain language.

## Safety rules

- Run read-only connection checks only.
- Never run Meta create, update, delete, publish, pause, archive, or budget-changing commands.
- Never print, echo, inspect, summarize, or commit credential values.
- Never display `.env` or `.skool-data/credentials.json` contents.
- Treat `ACCESS_TOKEN` and `SKOOL_AUTH_TOKEN` as secrets.
- Ask participant to edit cloud environment settings when credentials are missing or rejected. Do not ask them to paste secrets into chat.
- Do not commit generated runtime files.

## Commands

Install tools:

```bash
./scripts/setup.sh
```

Check connections:

```bash
./scripts/doctor.sh
```

Tests:

```bash
python3 -m unittest discover -s tests -v
```
