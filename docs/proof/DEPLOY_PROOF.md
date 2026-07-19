# Alibaba Function Compute — live deployment proof

URL: https://tarmac-xceukceokg.ap-southeast-1.fcapp.run
Region: ap-southeast-1  ·  Runtime: python3.10 (managed)  ·  Captured: 2026-07-19

## GET /health
```json
{
  "status": "ok"
}```

## GET /verify  (re-verifies the committed LIVE qwen3.7 run, in the cloud)
```json
{
  "checks": [
    {
      "detail": "chain ok (324 entries, head e261ceab70a8900e...)",
      "name": "chain",
      "ok": true
    },
    {
      "detail": "replay reproduces the recorded manifest byte-for-byte",
      "name": "I5.replay",
      "ok": true
    },
    {
      "detail": "no resource over capacity",
      "name": "I1.capacity",
      "ok": true
    },
    {
      "detail": "no beneficiary double-allocated in a group",
      "name": "I1.exclusivity",
      "ok": true
    },
    {
      "detail": "all 3 rulings cite >= 1 source",
      "name": "I3.rulings_cite_source",
      "ok": true
    },
    {
      "detail": "29 accepted reveals re-derive their commitment",
      "name": "I4.accepted_reveals_match",
      "ok": true
    },
    {
      "detail": "0 rejected reveals genuinely fail to match",
      "name": "I4.rejected_reveals_mismatch",
      "ok": true
    },
    {
      "detail": "zero crew duty violations in the final manifest",
      "name": "I2.crew_duty",
      "ok": true
    }
  ],
  "entries": 324,
  "manifest_hash": "58ce3bbcbc43bd90c53d5bef1068fb1ff221edbdd2009ef945e582eba2f0559b",
  "overall": "OK",
  "source": "live qwen3.7-plus / qwen3.7-max run (committed proof)"
}```

## GET /run?scenario=storm_dfw&seed=7  (fresh deterministic society)
```json
{
  "manifest_groups": {
    "gate:G1": 1,
    "gate:G2": 1,
    "gate:G3": 1,
    "gate:G4": 1,
    "gate:G5": 1,
    "gate:G6": 1,
    "hotel:block": 49,
    "seat:QW258": 20,
    "seat:QW338": 25,
    "seat:QW441": 9,
    "seat:QW519": 30,
    "seat:QW602": 40,
    "seat:QW777": 7
  },
  "manifest_hash": "c8494681334afd8c036ac6d22b9216afb02b937ce2cb4953c2e60b558efcc8be",
  "quiescent": true,
  "rounds_used": 5,
  "rulings": 2,
  "scenario": "storm_dfw",
  "seed": 7,
  "transport": "FakeQwen (offline deterministic \u2014 no key required)"
}```
