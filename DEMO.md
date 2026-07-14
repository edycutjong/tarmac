# Tarmac — Demo & Judge Guide

Everything here runs **offline, zero keys**, in a few seconds, byte-identical
every time (deterministic policy agents). Nothing touches the network.

## 0. Setup (30 seconds)

```bash
python3.12 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
```

## 1. The zero-key trust path (the one command to run)

```bash
./.venv/bin/python scripts/verify_offline.py
```

It disables all sockets, runs the full society on the committed fixture, replays
the manifest from the log, and re-checks invariants **I1–I5**. Exit `0` means the
run reproduced its own manifest byte-for-byte with no network. Expect:

```
replay reproduces the manifest (I5): c8494681334afd8c…
  [PASS] chain / I5.replay / I1.capacity / I1.exclusivity /
         I3.rulings_cite_source / I4.accepted_reveals_match / I2.crew_duty
OFFLINE VERIFICATION OK — 420 log entries, no network used
```

## 2. Watch the society negotiate

```bash
./.venv/bin/tarmac run --scenario storm_dfw --seed 7 --db runs/demo.db
```

Look for: **2 deadlocks**, **2 signed rulings**, **special-needs SLA 100%**,
**crew violations 0**, and quiescence in 5 rounds. Then contrast the baseline:

```bash
./.venv/bin/tarmac run --condition single --seed 7      # SLA 0%, crew violations 1
```

## 3. The measurable gain (the track's ask)

```bash
./.venv/bin/tarmac bench            # 3 conditions × 10 seeds → medians/IQR
```

Headline: **protected passengers stranded 3 (society) vs 17 (single planner)**,
100% special-needs SLA vs 0%, zero crew violations vs one. The `Society − mediator`
column proves the mediator is load-bearing (strip it and the society is *worse*
than a single planner).

## 4. Audit a ruling / replay the run

```bash
./.venv/bin/tarmac verify-log runs/demo.db      # exit 0, all invariants pass
./.venv/bin/tarmac replay runs/demo.db          # re-derives the identical manifest
```

Run `verify-log` on a *single*-planner run.db and watch **I2.crew_duty fail** — the
invariant catches the duty-illegal ferry the baseline scheduled.

## 5. Prove it isn't airline-shaped

```bash
./.venv/bin/python examples/meeting_rooms.py    # same ledger + deadlock detector, 20 lines
```

## 6. Witness real Qwen (needs a key — the one online step)

Everything above is offline and deterministic. To run the *same* society on the
live models, set a DashScope key and add `--live`:

```bash
export DASHSCOPE_API_KEY=sk-...            # https://dashscope.console.aliyun.com/apiKey
./.venv/bin/tarmac run --live --seed 7     # 5 role agents on qwen3.7-plus, mediator on qwen3.7-max
```

The `--live` transport (`LiveQwen`) is already exercised by **19 deterministic
tests** (`tests/test_qwen_transport.py`) that drive the DashScope wire format —
structured output, one-retry, the `enable_thinking` mediator flag, embeddings —
with a stub client, so this path is verified before it ever spends a token. What
a key adds is a live model's *judgement*; the ledger physics, sealed bids,
deadlock detection, signing and I1–I5 replay are byte-for-byte the same code as
the offline run above. **This is the one step a judge can't reproduce without
their own key** — the graded/witnessed magic is the offline ablation in §3.

## Video beat sheet (3:00)

`0:00` the 12-year-old & the DEPARTED board → `0:25` storm cancels QW2214, board
floods amber → `0:50` agent lanes argue: Crew vetoes the ferry with FAR 117.11;
Advocate claims 3 QW441 seats → `1:30` **deadlock**: board flashes red, two position
papers side-by-side, the mediator's signed ruling lands, board goes green → `2:10`
final manifest + **ablation table full-screen** ("this is the number the track
asked for: 3 vs 17") → `2:40` `verify_offline.py` exit 0 + replay → `2:55` close.
