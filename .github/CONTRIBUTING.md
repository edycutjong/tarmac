# Contributing

Thanks for your interest in improving Tarmac! 🎉

## Getting Started
1. Fork the repo and branch from `main`: `git checkout -b feat/your-feature`
2. Create a virtualenv and install in editable/dev mode:
   ```bash
   python3.12 -m venv .venv
   ./.venv/bin/pip install -e ".[dev]"
   ```
3. Run the offline demo to confirm your environment is sane: `./.venv/bin/python scripts/verify_offline.py`

## Before You Open a PR
- `ruff check .` passes (lint).
- `mypy src` runs clean, or new failures are explained in the PR description.
- `pytest --cov=tarmac_society` passes — add or update tests for any behavior change.
- `python scripts/verify_offline.py` still exits 0 (invariants I1–I5 hold).
- Keep commits conventional (`feat:`, `fix:`, `docs:`, `chore:`).

## Live Qwen Mode
Changes touching `src/tarmac_society/qwen/` should also be sanity-checked with
`--live` against a real `DASHSCOPE_API_KEY` when possible — the offline
`FakeQwen` transport intentionally can't catch prompt/schema drift against the
live API.

## Reporting Bugs / Requesting Features
Open an issue using the provided templates. Include repro steps (ideally a
`--seed`), expected vs. actual behavior, and environment details.
