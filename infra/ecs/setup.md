# Alibaba Cloud ECS — orchestrator setup (optional alternative)

> **Status: optional alternative — NOT the deployed target.** Tarmac is deployed
> live on **Alibaba Cloud Function Compute** (see [`infra/fc/`](../fc/)) — the
> `/verify` endpoint re-checks the committed run byte-for-byte in the cloud. This
> ECS runbook is only an optional persistent-host alternative and has not been
> stood up for this submission.
>
> Note on the live model path: the live Qwen path (`--live`) **has** been executed
> against DashScope — a full `qwen3.7-plus` / `qwen3.7-max` run is committed at
> [`docs/proof/live_run.db`](../../docs/proof/) and replays byte-for-byte
> (`tarmac verify-log docs/proof/live_run.db`). The graded path is still the
> offline deterministic run and the committed bench.

## Why you might use ECS instead of Function Compute

A society run is a long, stateful, multi-round loop that streams agent turns and
ledger events over SSE. A persistent host suits it better than a per-request
function, and it diversifies the portfolio's Alibaba surface (FC is used
elsewhere). One small instance is plenty — the workload is I/O-bound on the model
API, not CPU.

## 1. Provision

- **Instance:** `ecs.t6-c1m2.large` (2 vCPU / 4 GiB), Ubuntu 22.04, a public IP.
- **Security group:** inbound `22` (SSH, your IP only) and `8000` (the SSE API).
- **Region:** any DashScope-International-adjacent region for low model latency.

## 2. Install

```bash
sudo apt-get update && sudo apt-get install -y python3.12 python3.12-venv git
git clone https://github.com/edycutjong/tarmac.git && cd tarmac/build
python3.12 -m venv .venv
./.venv/bin/pip install -e ".[dev,live]"
./.venv/bin/pytest -q          # 329 green before you trust the box
```

## 3. Secrets (never commit these)

```bash
export DASHSCOPE_API_KEY=sk-…                 # live Qwen agents
export TARMAC_SIGNING_KEY_HEX=$(openssl rand -hex 32)   # Ed25519 ruling key
```

Commit only the **public** key. Every ruling this instance signs is then
verifiable off-box against that pubkey (`verify_body`), so rulings are portable
audit artifacts independent of the run database.

## 4. Run a live society round

```bash
./.venv/bin/tarmac run --scenario storm_dfw --seed 7 --live --db runs/live.db
./.venv/bin/tarmac verify-log runs/live.db     # invariants still hold on a live run
```

## 5. Keep it running (systemd sketch)

```ini
# /etc/systemd/system/tarmac.service
[Service]
WorkingDirectory=/home/ubuntu/tarmac/build
Environment=DASHSCOPE_API_KEY=sk-…
Environment=TARMAC_SIGNING_KEY_HEX=…
ExecStart=/home/ubuntu/tarmac/build/.venv/bin/tarmac serve --host 0.0.0.0 --port 8000
Restart=on-failure
```

> `tarmac serve` (the SSE API surfacing `POST /runs`, `GET /runs/{id}/stream`,
> `GET /integrations/verify`) is **future work** — the CLI `run`/`bench`/`verify-log`
> commands are what exist today.

## Proof plan (for the submission, when provisioned)

A console recording of the instance running one live `tarmac run --live` round,
plus the resulting `runs/live.db` passing `tarmac verify-log`, and the signed
rulings verifying against the committed public key.
