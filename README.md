# Funnel Leak Hunt — Cloud Setup

Claude Code web package for installing and checking read-only Meta Ads and Skool connections.

This first release contains setup and connection checks only. It does not contain seven-day challenge lessons.

## Participant quick start

1. Open this repository in [Claude Code web](https://claude.ai/code).
2. Create a personal cloud environment named `Funnel Leak Hunt`.
3. Follow [`docs/cloud-environment-setup.md`](docs/cloud-environment-setup.md).
4. Start a session using that environment.
5. Tell Claude:

   > Set up my Funnel Leak Hunt connections.

Claude will run:

```bash
./scripts/setup.sh
./scripts/doctor.sh
```

Successful output ends with:

```text
Connections ready. Both checks were read-only.
```

## Included tools

- `meta-ads==1.1.0`, installed from PyPI.
- `skool-data==0.1.1`, vendored under `tools/skool-cli/`.
- Cloud credential adapter that removes HAR import from this setup flow.
- Read-only Meta reporting commands:
  - `./scripts/ads-today.sh`
  - `./scripts/ad-spend.sh "DD-MM-YYYY"`
  - `./scripts/ads-help`

See [`docs/ads-cli-commands.md`](docs/ads-cli-commands.md) for command options and examples.

## Security

Credentials belong in a personal Claude cloud environment, never Git. Current CLIs must read credentials inside their process, so this pilot uses ordinary environment variables. Claude and commands running in that environment can read them. Use limited, revocable credentials and archive the environment after the challenge.

Generated `.env`, `.skool-data/`, and `.venv/` paths are ignored by Git. Setup never prints secret values.

## Maintainer verification

```bash
python3 -m unittest discover -s tests -v
bash -n scripts/setup.sh scripts/doctor.sh
python3 -m compileall -q scripts tests
```
