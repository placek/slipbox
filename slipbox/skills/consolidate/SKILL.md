---
name: consolidate
description: Maintain the topic map periodically — split oversized topics, add overview entries, and grow the bookmark hierarchy by the single-parameter-N rule, without ever dropping a note reference. Triggered by slipbox:consolidate or "tidy the index / topic map".
version: 0.1.0
author: Paweł Płaczyński
metadata:
  hermes:
    tags: [slipbox, index, bookmarks, consolidation, maintenance]
    requires_toolsets: [slipbox]
---

# slipbox:consolidate — maintain the bookmark hierarchy

## When to use
`slipbox:consolidate`, "tidy the topic map", or periodically as the store grows.
`index.md` is a maintained hierarchy of *bookmarks* — each says "the next ~N notes
are about X" until the next entry — and it is consolidated periodically, not per
note. This is that pass.

## The single rule (parameter N)
The structure is governed by one parameter, and the rule is identical for a small
store and a large one:
- an **interval longer than N** splits in two;
- a **level with more than ~N entries** gains a level above it.
The consequence is the scaling property: positional search always descends to a
**leaf pool of ~50 notes**, whatever the store's size. The tradition's *overview
note* — a map gathering links to up to ~25 related notes — is exactly an
`index.md` entry, and that size is what triggers a split.

## Procedure

1. **Survey.** `slipbox_index` for the current tree and `slipbox_store` for the
   Folgezettel order. For each topic, derive its interval `[entry, next-entry)` and
   count the notes and child bookmarks it covers. Flag:
   - topics whose interval or link count exceeds the threshold (candidates to
     **split**),
   - levels with more than ~N sibling entries (candidates to gain a **parent
     level**),
   - large clusters with no overview entry (candidates for a new **overview**).

2. **Propose, then confirm.** Show the reviewer the specific changes ("split
   'Attention' into 'Attention › Deep work' and 'Attention › Distraction'"). This
   reshapes navigation, so name the concrete moves before making them.

3. **Add incrementally where you can.** To place an existing note under a new
   nested topic, `slipbox_index_add` (it creates missing levels). Use this for new
   overview entries and deeper levels without touching the rest.

4. **Restructure with a full rewrite when needed.** To split or re-parent, compose
   the new `index.md` and call `slipbox_index_write`. It reports any `[[id]]` that
   was present before and is now gone — **that list must be empty**: consolidation
   *moves* a note to a better bookmark, it never drops one. If the guard reports a
   loss, you made a mistake — restore it.

5. **Report** the reshaping: topics split, levels added, overviews created, and the
   new bookmark count. Confirm the leaf pools are back under the threshold.

## Rules
- `index.md` is a **rebuildable derivative** — the store note IDs are the truth, so
  a rewrite is safe *as long as no reference is lost*.
- Never renumber or edit store notes to fit the map — the map serves the notes.
- Keep topic wording stable where you can; the structural layer matches queries by
  wording overlap.
