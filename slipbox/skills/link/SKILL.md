---
name: link
description: Place an accepted atom into the store under the Folgezettel ID it was assigned at adapt — persist applies that proposal verbatim, and `after` is an override for relocation only — and add it to the topic map. Triggered by slipbox:link [entry] / slipbox:persist, and by the persist job.
version: 0.2.0
author: Paweł Płaczyński
metadata:
  hermes:
    tags: [slipbox, CARP, persist, folgezettel, placement]
    requires_toolsets: [slipbox]
---

# slipbox:link — CARP Stage 4 (placement)

*Alias: `slipbox:persist`.*

## When to use
`slipbox:link` (place every accepted atom) or `slipbox:link <entry>`. Also the
single-instance persist job. Persist **does not search** — every atom already
carries the **proposed ID** it was assigned at adapt, so persist simply *applies*
it. You only find a placement yourself when a human is overriding the proposal.

## Procedure

1. **Take the accepted entries.** `slipbox_stage status=accepted`. Each shows its
   `proposed_id` — the Folgezettel ID it will take in the store.

2. **Persist — apply the proposed ID.** `slipbox_persist`:
   - `ident` — the stage entry,
   - (leave `after` empty to **apply the proposed ID verbatim** — the normal case),
   - `after` / `new_thread=true` — *only* when a human is relocating the atom away
     from its proposal (persist then re-derives the position after that target),
   - `topic` — the nested `index.md` path for a new thread (e.g.
     `["Attention", "Deep work"]`),
   - `rationale` — why here; it goes into the commit message.
   The tool renames the file to the ID, moves its vector `vec_stage → vec_store`
   unchanged, moves its attachments, updates the topic map, and commits. If the
   proposed slot was taken since adapt (a batch race), persist reallocates after
   the proposal's basis and reports it.

3. **Precedence.** An explicit `after`, or the reviewer's `link_after` /
   `variant_of` override, wins over the proposal; otherwise the proposed ID is
   applied. Validation is trivial because the store is append-only.

4. **Report** each placement: the store ID, what it follows, and the topic entry.

## Rules
- **Placed notes are immutable** — never renumber or edit them afterwards. A
  branch descends below its predecessor (`21` → `21-a`); an independent next
  thought opens a new top-level number — branching without renumbering.
- Never invent an ID by hand — only `slipbox_persist` allocates, under its lock.
- A misplacement is repaired by a *new* note (`supersedes [[ID]]`), never by
  moving an existing one; wikilinks carry the connection independent of position.
- Rejected atoms are cleaned up by the persist job, not here.
