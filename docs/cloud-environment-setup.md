# Claude Code Web Environment Setup

Create one personal environment and reuse it throughout challenge.

## 1. Create environment

In Claude Code web, open environment selector and create:

```text
Name: Funnel Leak Hunt
```

## 2. Configure network

Choose **Custom** network access. Allow:

```text
graph.facebook.com
api2.skool.com
www.skool.com
```

Keep default package-manager domains enabled so Python packages can install.

## 3. Leave API credentials empty

Do not use **API credentials** for this pilot. Protected credentials are safer, but current Meta and Skool CLIs need credential values inside their own process and cannot consume Claude's proxy-injected credentials reliably.

## 4. Add environment variables

Under **Environment variables**, add:

```env
ACCESS_TOKEN=your_meta_system_user_token
AD_ACCOUNT_ID=your_numeric_ad_account_id
SKOOL_AUTH_TOKEN=your_skool_auth_token_cookie_value
SKOOL_COMMUNITY_ID=your_skool_community_id
```

Optional:

```env
SKOOL_PROFILE_DATA_URL=full_skool_next_data_profile_url
```

Use numeric Meta account ID with or without `act_`; setup normalizes it.

### Credential warning

Environment variables are visible to Claude and commands running inside this environment. Use limited, revocable credentials. Never paste them into chat, source files, or Git. Archive environment and revoke challenge credentials when finished.

## 5. Start setup

Save environment. Start a new session for this repository using `Funnel Leak Hunt`. Ask:

```text
Set up my Funnel Leak Hunt connections.
```

Claude installs tools and runs read-only checks.

## Expected result

```text
PASS  installation
PASS  Meta Ads read access
PASS  Skool read access

Connections ready. Both checks were read-only.
```

## Fixing failures

- **Missing cloud environment variables:** edit environment, save, then start new session.
- **Meta rejected credentials:** verify token belongs to system user with ad-account read access.
- **Skool rejected credentials:** copy fresh `auth_token` cookie value while logged into Skool as community admin.
- **Network error:** verify three required domains are allowed.

Never send credential values in Claude conversation.
