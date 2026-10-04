---
name: cloud-setup
description: Install and verify Funnel Leak Hunt Meta Ads and Skool connections in Claude Code web. Use when participant asks to set up, connect, check, or start challenge tools.
---

# Cloud Setup

## Rules

- Never ask participant to paste credentials into chat.
- Never print or open credential files.
- Run only commands listed below.
- Connection checks are read-only.

## Procedure

1. Tell participant setup checks four cloud environment variables without displaying them.
2. Run:

   ```bash
   ./scripts/setup.sh
   ```

3. Run:

   ```bash
   ./scripts/doctor.sh
   ```

4. Interpret results:
   - Both connections pass: say setup is ready.
   - Missing variables: ask participant to add named variables under cloud environment **Environment variables**, then start a new session.
   - Meta fails: ask participant to verify Meta system-user token, ad-account access, and numeric account ID.
   - Skool fails: ask participant to verify `auth_token`, community ID, and that account is community admin.
   - Network failure: ask participant to allow `graph.facebook.com`, `api2.skool.com`, and `www.skool.com` in environment network settings.

Do not attempt alternate API commands or mutations during recovery.
