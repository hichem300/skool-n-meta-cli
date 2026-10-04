# Funnel Leak Hunt Cloud Setup Design

## Goal

Provide one GitHub-ready repository that helps a nontechnical challenge participant install and verify read-only Meta Ads and Skool CLI connections inside Claude Code web.

## Scope

This first version contains cloud setup and connection checks only. Seven-day challenge lessons and diagnosis workflows are outside scope.

## Architecture

The repository vendors a pinned snapshot of `skool-data` and installs `meta-ads==1.1.0` from PyPI into a repository-local virtual environment. A Claude skill guides setup. Small configuration scripts convert cloud environment variables into the private runtime files expected by each CLI. One doctor command performs read-only connection checks and prints no credential values.

## Authentication

Current CLIs need credentials inside their process, so participants use Claude cloud environment variables rather than protected API credentials. Required values are:

- `ACCESS_TOKEN`
- `AD_ACCOUNT_ID`
- `SKOOL_AUTH_TOKEN`
- `SKOOL_COMMUNITY_ID`

The setup creates ignored, mode-0600 `.env` and `.skool-data/credentials.json` files inside the disposable cloud VM. Credentials are never committed or printed. The Skool cloud adapter removes the HAR requirement by creating the same minimal credential model directly from `SKOOL_AUTH_TOKEN` and `SKOOL_COMMUNITY_ID`.

## User flow

1. Participant connects GitHub and opens this repository in Claude Code web.
2. Participant creates a personal cloud environment with access to Meta and Skool domains.
3. Participant adds four required environment variables.
4. Participant asks Claude to run cloud setup.
5. Claude reads `.claude/skills/cloud-setup/SKILL.md`, runs `scripts/setup.sh`, then `scripts/doctor.sh`.
6. Doctor reports separate PASS/FAIL results for local installation, Meta access, and Skool access.

## Safety

- All API checks are read-only.
- No create, update, or delete Meta commands exist in doctor flow.
- Secret-bearing runtime files are Git-ignored and mode 0600.
- Error output is sanitized for access tokens, bearer tokens, and Skool cookies.
- Documentation warns participants that ordinary cloud environment variables are readable by commands and Claude sessions using that environment.

## Validation

Automated tests verify runtime-file creation, file permissions, missing-variable behavior, and secret redaction. Shell syntax and Python compilation checks cover entry scripts. Live API checks require participant credentials and are verified through `scripts/doctor.sh` in Claude Code web.
