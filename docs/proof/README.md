# Live-Qwen run proof

`live_run.db` is a **fully live** society run — every agent turn and mediator
ruling came from real DashScope calls (`qwen3.7-plus` role agents,
`qwen3.7-max` mediator), not the offline `FakeQwen`. Reproduce the verification:

```bash
tarmac verify-log docs/proof/live_run.db
```

`live_run.verify.txt` is the captured output: **324 entries, all invariants
PASS**, including `I5.replay: replay reproduces the recorded manifest
byte-for-byte` — i.e. the live run is cryptographically replayable, identical to
its recorded manifest, exactly as an offline run is.

Scenario `storm_dfw`, seed 7, 4 rounds, 3 mediator rulings.

> The `--live` path was hardened to make this possible: role-agent calls run
> with Qwen3 "thinking" disabled (it otherwise spends thousands of reasoning
> tokens per call and times out on the large negotiation-state prompts), a
> per-request client timeout guards against a single slow call hanging the run,
> and the mediator's ruling application now drops citations to source ids that
> aren't in the regulation library (a live model can invent them) instead of
> aborting the whole run.
