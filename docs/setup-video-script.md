# Setup Video Script

Target length: 5–7 minutes.

## 1. Open repository

Show Claude Code web, connect GitHub, select Funnel Leak Hunt repository.

## 2. Create environment

Open cloud selector → **Create environment**. Name it `Funnel Leak Hunt`.

Set network to **Custom**. Add:

```text
graph.facebook.com
api2.skool.com
www.skool.com
```

Keep default package-manager access enabled.

## 3. Add connections

Scroll past **API credentials**. Explain that pilot CLIs currently use environment variables.

Add four values under **Environment variables**:

```env
ACCESS_TOKEN=
AD_ACCOUNT_ID=
SKOOL_AUTH_TOKEN=
SKOOL_COMMUNITY_ID=
```

Show where each value comes from, but blur every token and cookie. Tell viewers never to paste secrets into Claude chat.

## 4. Run setup

Save environment. Start new session with it selected. Enter:

```text
Set up my Funnel Leak Hunt connections.
```

Show successful PASS lines without opening generated credential files.

## 5. Recovery

Briefly show environment edit screen. Explain: rejected credential → replace value there → save → start new session → rerun setup.

End when both read-only checks pass.
