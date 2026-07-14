# Friction Log — building Tarmac on Qwen Cloud

Honest notes from the build. The interesting friction was mostly at the seams
between *deterministic ledger physics* and *LLM-shaped negotiation* — which is
exactly where an agent society lives.

## 1. Structured output is the contract, not a suggestion

The ledger executes `Claim` / `Position` / `Ruling` **mechanically** — a
malformed object is rejected, not "interpreted." So the structured-output
contract has to be strict on both ends:

- The pydantic wire types (`schemas.py`) are the single source of truth. In live
  mode we embed `Model.model_json_schema()` in the prompt and validate with
  **exactly one reject-and-retry** (`LiveQwen._structured`), quoting the
  validation errors back to the model. A second failure is a hard error for the
  mediator (a ruling must never be improvised) and a safe no-op for a role agent
  (the round just proceeds without it).
- `extra="forbid"` on every model caught more than one hallucinated field during
  prototyping. Better a reject-and-retry than a silently-dropped key.
- Cross-field invariants (`qty == len(beneficiaries)`, "a blocking position must
  cite a source") live in the schema as model validators, so they hold for
  **both** the LLM path and the offline policy path. One contract, two brains.

## 2. Context cache demands prefix discipline

The 4k-token disruption prefix (the shared `storm_dfw` state) is re-read on every
one of ~60 agent turns per run. For the context cache to actually hit, that prefix
must be **byte-stable** across turns:

- `view_to_prompt_dict` puts `shared_scenario` **first** and serializes every dict
  with `sort_keys=True`, so the cacheable prefix never reorders.
- Private per-agent briefs go **after** the shared prefix, never interleaved — an
  agent-specific token early in the prompt would bust the shared cache for everyone.

Without this the 60-turn society costs ~10× and the ablation becomes uneconomical;
it's the difference between "runs once" and "can be measured."

## 3. Sealed bids exposed a self-collision bug (offline)

Contested rounds commit `SHA256(claim‖nonce)` before any reveal. Building the
deterministic Rebooking policy, we hit a subtle bug that the sealed-bid structure
made visible: the policy proposed **two claims in one round** (all 9 QW441 seats
*and* 15 QW519 seats) whose beneficiary lists **overlapped**, because the second
claim's candidate list didn't yet see the first (the claims weren't in
`my_granted` — they were being built in the same call). At reveal, the seat
exclusivity group blocked the second claim all-or-nothing, cascading into empty
flights (the society stranded *more* than a greedy planner). The fix was one
`taken` set threaded through the round's proposals. Lesson: in a sealed-bid
world, an agent must reconcile its *own* simultaneous bids before committing —
the ledger will not do it for you after reveal.

## 4. A mediator ruling revokes; it does not re-seat

`RulingOp` can `revoke` / `grant` / `void` existing claims — it deliberately
**cannot mint a new claim**. So when the Duty-Manager bumps a low-priority
passenger off QW441 to seat the courier, that passenger is revoked, not
teleported; a later Rebooking fill-round re-seats them on QW519/QW602. Keeping the
mediator's vocabulary minimal (three verbs) keeps rulings auditable and keeps the
"who re-books the bumped passenger" logic in the agents where it belongs — but it
means the round loop must give the society enough rounds to converge. It does
(quiescence at round 5 of 6 on the fixture).

## 5. Determinism is a feature you have to defend

"Seeded and reproducible" is easy to claim and easy to break. Two things that
mattered:

- Commitment nonces come from a **seeded** `random.Random` in offline mode
  (`make_nonce(rng)`), so two runs produce byte-identical chains — not just
  identical manifests. The determinism test asserts equal `chain_head`, not just
  equal manifest hash.
- The hash-chained log carries **logical time only** (round + sequence), never a
  wall clock, so nothing timestamp-shaped leaks into a hash.

## 6. Model selection is a domain argument

`qwen3.7-plus` for the five role agents and `qwen3.7-max` + thinking for the
mediator isn't a cost dodge — it's the shape of the problem. Five personas holding
a line across 60 turns is a *persona-stability* task (plus tier is plenty and
6× cheaper to benchmark); adjudicating five conflicting position papers against
duty arithmetic and re-accommodation regs is the one genuinely hard reasoning step
(max tier + thinking). Using `max` everywhere would have made the ablation cost
more than the prize was worth to iterate on.
