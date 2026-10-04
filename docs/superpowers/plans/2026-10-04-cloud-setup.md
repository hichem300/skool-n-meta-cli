# Funnel Leak Hunt Cloud Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build GitHub-ready Claude Code web setup package with read-only Meta Ads and Skool connection checks.

**Architecture:** Vendor pinned `skool-data` source, install Meta Ads CLI from pinned PyPI version, and adapt cloud environment variables into CLI runtime files. Expose setup through one Claude skill and one doctor command.

**Tech Stack:** Bash, Python 3.11+, `meta-ads==1.1.0`, vendored `skool-data`, unittest.

---

### Task 1: Repository documentation

**Files:**
- Create: `README.md`
- Create: `CLAUDE.md`
- Create: `docs/cloud-environment-setup.md`
- Create: `.env.example`
- Create: `.gitignore`

- [ ] Document exact participant setup flow and credential warning.
- [ ] Define Claude's read-only setup behavior.
- [ ] List required environment variables without real values.

### Task 2: Dependency installation

**Files:**
- Create: `requirements.txt`
- Create: `scripts/setup.sh`
- Vendor: `tools/skool-cli/`

- [ ] Pin Meta CLI version and install vendored Skool CLI.
- [ ] Create repository-local `.venv` using `python3`.
- [ ] Verify both CLI entry points after installation.

### Task 3: Cloud credential adapters

**Files:**
- Create: `scripts/configure_runtime.py`
- Test: `tests/test_configure_runtime.py`

- [ ] Validate four required environment variables.
- [ ] Write ignored Meta `.env` and Skool credential JSON with mode 0600.
- [ ] Never print credential values.
- [ ] Test valid configuration, missing values, and permissions.

### Task 4: Connection doctor

**Files:**
- Create: `scripts/doctor.py`
- Create: `scripts/doctor.sh`
- Test: `tests/test_doctor.py`

- [ ] Run one read-only Meta campaign-list request.
- [ ] Run Skool's online doctor against analytics overview.
- [ ] Sanitize secrets from subprocess errors.
- [ ] Return nonzero when either connection fails.

### Task 5: Claude setup skill

**Files:**
- Create: `.claude/skills/cloud-setup/SKILL.md`

- [ ] Tell Claude to install, configure, and run doctor in order.
- [ ] Require read-only commands and prohibit displaying secrets.
- [ ] Give concise recovery steps for missing or rejected credentials.

### Task 6: Verification

- [ ] Run `python3 -m unittest discover -s tests -v`.
- [ ] Run `bash -n scripts/setup.sh scripts/doctor.sh`.
- [ ] Run `python3 -m compileall -q scripts tests`.
- [ ] Run `./scripts/setup.sh` and verify CLI versions.
- [ ] Commit complete setup package.
