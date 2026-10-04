# Ads reporting commands

Read-only Meta Ads reports. Run commands from repository root after setup.

## `ads-today.sh`

Report today, date range, or one campaign. Default level: campaign.

```bash
# Today, all active campaigns
./scripts/ads-today.sh

# Today, broken down by ad
./scripts/ads-today.sh --level ad

# Today, one exact campaign ID or exact campaign name
./scripts/ads-today.sh "My Campaign"

# Inclusive date range, all campaigns
./scripts/ads-today.sh --range "01-09-2025 to 07-09-2025"

# Date range for one campaign, broken down by ad
./scripts/ads-today.sh --range "01-09-2025 to 07-09-2025" --level ad "My Campaign"

# Maximum available range
./scripts/ads-today.sh --range max
```

- `--level campaign|ad` is optional; default: `campaign`.
- Campaign selector is optional; use exact campaign ID or exact name.
- Date range format: `"DD-MM-YYYY to DD-MM-YYYY"`.

## `ad-spend.sh`

Show daily account spend for one date or date range.

```bash
./scripts/ad-spend.sh 05-09-2025
./scripts/ad-spend.sh "01-09-2025 to 07-09-2025"
```

Exactly one argument required: `DD-MM-YYYY` or `"DD-MM-YYYY to DD-MM-YYYY"`.

## `ads-help`

Show this reference. Uses Glow when available, plain text otherwise.

```bash
./scripts/ads-help
```
