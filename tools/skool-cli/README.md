# skool-data

`skool-data` is a local CLI for capturing analytics from a Skool community you own. It replays the dashboard's repeatable HTTP requests, preserves immutable raw JSON responses, and normalizes recognized metrics into SQLite.

## Security

HAR files and `.skool-data/` contain private authentication and business data. They are ignored by Git and must not be uploaded, committed, or shared. The CLI never stores your Skool password. AWS WAF headers and cookies are discarded because the tested analytics requests do not require them.

When authorization expires, capture a fresh analytics HAR in browser DevTools and import it again. Skool's internal API is not a public stable API and can change without notice.

## Install

```bash
cd /root/business/PROJECTS/skool-cli
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## Import your authenticated request

Chrome exports sanitized HAR files by default, removing the cookie or authorization header required by direct requests. Export an authenticated HAR as follows:

1. Open DevTools settings.
2. Under **Preferences → Network**, enable **Allow to generate HAR with sensitive data**.
3. Open the Skool analytics dashboard and record its Fetch/XHR requests.
4. In the Network request list, right-click and choose **Copy → Save all listed as HAR (with sensitive data)**.
5. Save the private file in this project, where `*.har` is ignored.

Then run:

```bash
.venv/bin/skool-data auth import-har www.skool.com.har
```

The importer rejects HARs without a Skool authentication cookie or authorization header. It keeps only the API base URL, community ID, and allowlisted request headers needed for direct HTTP capture, while removing AWS WAF credentials. Runtime credentials are written with owner-only permissions under `.skool-data/credentials.json`.

## Verify and capture

```bash
.venv/bin/skool-data doctor
.venv/bin/skool-data capture
.venv/bin/skool-data snapshots
```

Use `doctor --offline` to check credentials and local storage without contacting Skool.

In a color terminal, capture lifecycle events use one live-updating Rich progress display. Redirected output and `--no-color` remain plain and non-interactive. The default report is compact; use `capture --verbose` for the full business report, including `Current Traffic Sources`. Skool does not expose that chart's date range, so it is labeled `Skool window unspecified` rather than weekly.

Use plain output when needed:

```bash
.venv/bin/skool-data capture --no-color
```

Rich also respects the standard `NO_COLOR` environment variable and automatically adapts when output is redirected.

A capture can be:

- `complete`: every configured endpoint succeeded;
- `partial`: at least one endpoint succeeded and at least one failed;
- `failed`: no analytics endpoint succeeded.

Successful source responses remain under `.skool-data/data/raw/<capture-id>/` even when another endpoint fails.

## Skool Discovery

Capture public Skool Discovery rankings:

```bash
.venv/bin/skool-data discovery
.venv/bin/skool-data discovery --page 3
.venv/bin/skool-data discovery --query claude
.venv/bin/skool-data discovery --query claude --page 2 --format json
.venv/bin/skool-data discovery --no-color
```

`--page`/`-p` selects a results page and `--query`/`-q` searches community names and keywords. Requests use English results and trending order for paginated browsing. The command fetches Discovery anonymously. It does not send imported analytics cookies, authorization headers, or AWS WAF values. Each run is saved in dedicated SQLite tables with an immutable sanitized artifact containing only the extracted public listing data. Rich output shows a ranked terminal table; JSON output provides community identity, URL, rank, description, members, price, membership model, and tags.

## Daily analytics

Fetch four values for the current UTC day:

```bash
.venv/bin/skool-data daily
```

Or request an explicit UTC date:

```bash
.venv/bin/skool-data daily 30-08-2026
```

The Rich report includes group visitors, group activity, your personal profile activity, and total members. Skool buckets activity dates in UTC, so `daily` uses UTC rather than the machine's local timezone.

Skool retains different history windows. Visitor and member totals are generally available for about 30 days, while group and profile activity cover roughly one year. Dates outside a source's window remain visible as `Unavailable`; other values are still displayed and persisted.

The profile activity source derives the stable profile page from the imported route and reads the page's embedded Next.js data directly. A Skool deployment changing its Next.js build ID therefore does not require another HAR import.

Daily runs store normalized observations and minimal source fragments under `.skool-data`. They never retain the complete profile response, unrelated posts, email addresses, or authentication values. Use `daily --no-color` for plain output.

## Classroom module export

Export one accessible classroom module to Markdown by pasting its full URL:

```bash
.venv/bin/skool-data classroom export \
  "https://www.skool.com/group/classroom/course?md=module" \
  --output ./classroom-exports
```

The export mirrors Skool's course and module organization:

```text
classroom-exports/
└── course-name/
    └── 02-module-name/
        ├── module.md
        ├── video/video.mp4
        ├── transcript/transcript.vtt
        ├── transcript/transcript.md
        ├── assets/
        └── .skool-export.json
```

Videos are downloaded at the best available quality. When captions are available, the original WebVTT transcript and a readable Markdown transcript are saved. Attached resources remain under the module's `assets/` category. Video merging uses `ffmpeg`, which must be installed on the machine.

The exporter uses the existing `auth_token` in `.skool-data/credentials.json` and requests the stable classroom page directly. It does not read or require a HAR at runtime. Your account must already have access to the requested module.

Each module has a private local manifest containing remote fingerprints and local checksums. Running the same command again inspects the remote video, transcript, and assets, skips unchanged files, and downloads only changed or missing artifacts. Locally corrupted managed files are repaired. Pass `--force` only when you intentionally want to redownload every managed artifact. Unrelated files inside the module directory remain untouched.

## Query and export

```bash
.venv/bin/skool-data query --metric members.total
.venv/bin/skool-data query --metric members.total --format json
.venv/bin/skool-data query --metric members.total --since 30-08-2026
.venv/bin/skool-data export \
  --metric members.total \
  --format csv \
  --output .skool-data/exports/members.csv
```

Exports refuse to overwrite existing files unless `--force` is provided. Supported export formats are CSV and JSON.

## Captured API families

The initial adapters cover:

- analytics overview: member count, MRR, and retention;
- growth overview: visitors, signups, and new MRR;
- signups by source;
- signups by day;
- MRR movement: churn, downgrade, existing, new, reactivation, upgrade, and net MRR by month;
- cashflow: one-time, annual, and monthly revenue, affiliate payouts, fees, refunds, disputes, and net by month;
- unit economics: ARPU and LTV by month;
- 30-day admin member and activity metrics.

Routine capture uses direct HTTP requests. It does not scrape the rendered dashboard or run browser automation.

## Claude Cowork plugin pilot

Build the self-contained community-testing plugin:

```bash
.venv/bin/python3 scripts/build_cowork_plugin.py
```

This creates:

- `dist/skool-analytics-plugin.zip`
- `dist/skool-analytics-plugin.zip.sha256`

Members upload the ZIP through Cowork **Customize**, attach their own sensitive Skool HAR for each analysis session, and ask for daily or full analytics. The plugin requires explicit consent, emits sanitized JSON, and removes extracted temporary credentials when its runner exits.

See `docs/cowork-plugin-pilot.md` for member setup, security warnings, example prompts, troubleshooting, the setup-video script, and pilot feedback questions.

## Development

```bash
.venv/bin/pytest -v
```

Tests use synthetic HAR and HTTP fixtures. They do not contact Skool or require real credentials.
