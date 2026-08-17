---
name: oversight
description: The admin's domain-drift oversight — read per-contributor scope statistics and backlog trends, surface where the base is drifting out of its charter, and guide pattern-level decisions (tighten the charter, mute a source, spin off a store). Triggered by slipbox:oversight or "how is the base drifting / admin review".
version: 0.1.0
author: Paweł Płaczyński
metadata:
  hermes:
    tags: [slipbox, admin, domain-charter, scope, drift]
    requires_toolsets: [slipbox]
---

# slipbox:oversight — domain-drift oversight (admin)

## When to use
`slipbox:oversight`, "how is the base drifting?", or a periodic admin review. The
one genuinely multi-user problem is **domain drift**: a base founded for one
domain being fed content from outside it. It is handled like duplicates — *the
system signals, a human decides*. This skill puts the signal in front of the admin
and helps them act; it does not act on its own.

## The signal
Adaptation classifies every source against the `SOUL.md` **domain charter** —
`scope: in / adjacent / out`, one sentence of rationale, at no extra model cost.
`slipbox_status` aggregates this into **per-contributor scope statistics**, which
is precisely the drift-oversight signal. `adjacent` is healthy — it is where
dis-confirming material lives; a rising tide of `out` from one contributor is the
thing to catch.

## Procedure

1. **Read the charter.** `slipbox_show SOUL.md` (or read `SOUL.md`). Restate what
   the base *is*, *is not*, and what counts as *adjacent* — the oversight is only
   as good as the charter is explicit.

2. **Read the signal.** `slipbox_status`: `stage.by_scope`,
   `stage.scope_by_contributor`, and the inbox backlog per contributor with its
   trend. Identify:
   - contributors whose share of `out` / `adjacent` is rising,
   - sources or topics that keep arriving out-of-scope,
   - backlog that is rotting (old, unreviewed) — a separate health problem.

3. **Interpret, do not judge people.** `captured_by` is provenance of interest,
   never authorship — every atom's author is the agent. Framing is "this *material*
   is drifting", not "this person is wrong".

4. **Recommend a pattern-level action**, and carry out the ones you can:
   - **Sharpen the charter** — if `out` reflects a real but unnamed neighbour,
     edit the *Adjacent* section of `SOUL.md` to name it (the cheapest fix; it
     operationalises the relevance filter for every future adaptation).
   - **Mute a source** — when one source floods the base off-topic, agree a policy
     (stop capturing it) and record it in `SOUL.md`.
   - **Spin off a store** — when a topic has genuinely outgrown the domain, propose
     a separate slipbox for it (federation is the visibility/boundary tool), and
     note the decision.

5. **Report** the drift picture and the decisions taken or proposed, and update
   `SOUL.md` accordingly so the next adaptation reads the sharper charter.

## Rules
- Signal, never automatic enforcement — only a human changes the charter or mutes a
  source.
- `adjacent` is deliberately kept and flagged; do not treat it as a problem to
  eliminate — cutting adjacency cuts the base's contradicting material.
- The charter and the skill prompts are the store's identity; version them in the
  repo alongside `SOUL.md`.
