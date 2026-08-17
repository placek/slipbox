---
name: review
description: Walk a human through the review gate — accept or reject whole per-source batches of pending atoms, and on request shape titles, placement and duplicates. Triggered by slipbox:review and by the morning digest.
version: 0.1.0
author: Paweł Płaczyński
metadata:
  hermes:
    tags: [slipbox, CARP, review, quality-gate]
    requires_toolsets: [slipbox]
---

# slipbox:review — CARP Stage 3 (the quality gate)

## When to use
`slipbox:review`, or when a user answers the morning digest. This is the system's
one point of **deliberate shaping** and its **quality gate**: the permanent store
contains *exclusively human-approved content*. You present; the human decides. Do
not accept or reject on your own — you carry out the editor's verdict.

## Progressive disclosure
Keep the default flow a **batch decision with your best guesses preselected**.
Alternatives appear only when the reviewer expands an atom — never as blocking
questions.

## Procedure

1. **Show the queue, grouped per source.** `slipbox_stage status=pending`. Group
   by `source` and present each batch with, per atom: title, its **`proposed_id`**
   (the ID it will take in the store), scope, and any duplicate flag. Review
   accepts or rejects **whole per-source batches** with one decision; per-atom
   overrides are the exception.

2. **Lift the exceptions out of the default batch:**
   - **`scope: out`** — does not batch-accept; needs an explicit decision.
   - **`scope: adjacent`** — passes flagged, deliberately (adjacency is where
     dis-confirming material lives).
   - **potential duplicates** (`duplicate` set) — show side by side with the twin
     (`slipbox_show` both). The editor chooses: **reject** (only the *new* atom is
     ever removed, never the existing one), **accept as a variant** (a newer
     version of the same position — `variant_of=<twin-id>`, placed next to the
     original), or **accept as distinct** (a false alarm).

3. **On acceptance, move the note into the store with its ID applied.** This is
   the point of review: an accepted atom leaves `stage/` for `store/`, taking the
   proposed Folgezettel ID it was assigned at adapt. Do it in two steps per atom
   (or per batch):
   1. `slipbox_review status=accepted` (`decided_by`; and only if the editor
      *changes* the placement, `link_after=<id>` / `variant_of=<id>` /
      `new_thread_topic`),
   2. `slipbox_persist` — renames the file to the ID, moves it into `store/`
      (vector and attachments follow), and updates the topic map. With no
      override it **applies the proposed ID verbatim**; a review `link_after`
      re-derives the position. (The nightly persist job does this for any
      accepted atom you leave — `slipbox persist-accepted` — but on an
      interactive accept, persist now so the editor sees the final ID.)
   **Rejected** atoms: `slipbox_review status=rejected`, then `slipbox_purge_rejected`
   (removes the new atom, its attachments and its vector — never the twin).

4. **Offer shaping only when asked** (an atom is expanded):
   - **Title** — the editor picks one of the pre-generated `variants` (metadata).
   - **Placement** — accept the proposed ID, or pin a different one with
     `link_after` / `new_thread_topic` before persisting.
   - **Re-split** — "split differently" re-runs distillation against the retained
     original: hand off to `slipbox:readapt`. This exists only because the
     originals layer does.

5. **Report** the batch decisions and the **final store IDs** the accepted atoms
   received.

## Rules
- Only the human editor rejects; **the system never drops on its own**. The
  duplicate threshold is deliberately sensitive — a false alarm costs one
  expansion, a missed duplicate costs permanent noise.
- Decisions are frontmatter metadata — the only mutation `stage/` permits.
- Anyone may review any batch: rejecting an atom corrects a machine draft, not a
  colleague's prose.
