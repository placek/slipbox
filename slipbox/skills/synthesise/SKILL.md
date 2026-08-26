---
name: synthesise
description: Write a dated synthesis — an answer, an overview binding a cluster, or a comparison across threads — citing the atoms it rests on. Triggered by slipbox:synthesise, "keep this answer", or a stale synthesis reported by slipbox_drift.
version: 0.1.0
author: Paweł Płaczyński
metadata:
  hermes:
    tags: [slipbox, synthesis, retrieval, citations, overview]
    requires_toolsets: [slipbox]
---

# slipbox:synthesise — keep an answer, as a view over the store

## When to use
- After a `slipbox:search` that produced an answer worth keeping. A good answer
  is expensive: the hops were walked, the threads gathered, the citations placed.
  Letting it dissolve into chat history throws that away.
- To write an **overview note** binding a cluster (~25 related atoms) — the
  tradition's map, which belongs here rather than in `index.md`.
- To **compare two threads**, which no other structure expresses: positional IDs
  give threads, `index.md` gives topics, and a synthesis gives the cut across
  them.
- When `slipbox_drift` reports a synthesis that has fallen behind.

## The one rule everything else follows from
**A synthesis is never the proof of a claim — the proof is the atoms it cites.**
It is a *navigational object*: a lead for the next reader, not evidence. That is
why it needs no human review, and it is also the whole of your responsibility
here. An atom passes a human gate because it *is* the evidence; a synthesis does
not, because anything taken from it must still resolve to an atom. Write one that
cannot be resolved that way and you have removed the only thing that made
skipping review safe.

Concretely: every claim you write must be traceable to something in `cites`, and
`cites` must list what you actually read.

## Procedure

1. **Look before you write.** Run `slipbox_search` with the question. If the
   result carries a `synthesis_lead`, someone has already walked this road:
   - `drift: 0` — the earlier answer stands. **Do not write a second one.** Point
     at it, quote it, and stop. Two syntheses of one question with no new atoms
     between them is the near-duplicate problem, self-inflicted.
   - `drift: N` — read *only* the `new_atoms`, then write a synthesis that
     `supersedes` the old one. That is the cheap path this object exists for.
   Use `slipbox_syntheses` to see what already exists more broadly.

2. **Read every atom you intend to cite.** `slipbox_show` (or `slipbox_quote`).
   Citing from a search snippet is how a citation ends up not supporting the
   sentence attached to it. `slipbox_tree` pulls a whole thread when a candidate
   sits mid-argument.

3. **Name the tensions.** If two atoms you cite are joined by `contradicts`, say
   so in the prose — that edge is already visible to `grep`; your job is to make
   it visible to a reader. Never average two positions into a hedge. Where the
   store genuinely disagrees with itself, the synthesis reports the disagreement
   and its dates.

4. **Write it.** `slipbox_synthesise` with:
   - `question` — the question as it was actually asked. This is what the lead
     channel matches future queries against, so a vague one makes the synthesis
     unfindable.
   - `title` — what it concludes, as a claim.
   - `body` — the document, each claim citing `[[id]]`.
   - `cites` — every atom it rests on. Required.
   - `supersedes` — the path of the synthesis this answers past, when step 1
     found one.

5. **Report** what it cites, what it supersedes, and any atom you deliberately
   left out with the reason. It commits immediately; there is no review queue.

## Rules
- **Cite only placed store atoms.** The tool refuses unknown IDs — do not work
  around that by dropping the citation and keeping the sentence.
- **Never turn a synthesis into an atom.** Flow is one-way: atoms compose into
  syntheses. A synthesis distilled back into the store would be the system
  feeding on its own summaries.
- **Do not synthesise from `source/.attachments/` originals.** They are untrusted
  raw material behind a deliberate quarantine; a synthesis rests on reviewed
  atoms. If the original holds something the atoms miss, that is `slipbox:readapt`.
- **Superseding keeps, never replaces.** The earlier synthesis stays readable and
  dated, which is what lets the store show how its own view changed. Do not
  delete or edit it.
- **When the store does not answer, do not write one.** A synthesis of thin
  evidence is worse than none, because it looks like an answer for as long as it
  exists.
