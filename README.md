# slipbox — a curated, agent-operated knowledge base (hermes-agent plugin)

A Python hermes-agent plugin implementing the [Slipbox whitepaper](./slipbox-whitepaper.pdf) ([Typst source](./slipbox-whitepaper.typ)):
it inverts the usual retrieval paradigm by spending intelligence at **write time**
— distilling every captured source into atomic, human-reviewed notes placed in a
linearly ordered (Folgezettel) store of plain-text markdown under git — so that
retrieval becomes layered, auditable and nearly trivial.

The whole system is a git repository, one SQLite file, and three in-process
models. No external services.

## The write path — CARP

```
capture ─▶ inbox/ ─── adapt ──▶ stage/ ─── review ──▶ (accepted) ─── persist ──▶ store/
                         │                                                         (immutable,
                         └── source note + original → source/.attachments/          Folgezettel)
```

| Stage | Skill | What happens |
|-------|-------|--------------|
| 1 Capture | `slipbox:capture` | Raw material lands in `inbox/` verbatim, with attribution. |
| 2 Adapt | `slipbox:adapt` | Split into one-idea atoms in `stage/`; embed at once; scope-classify; cache placement; archive the original into cold storage. |
| 3 Review | `slipbox:review` | The human quality gate — accept/reject whole per-source batches; shape titles, placement, duplicates. |
| 4 Persist | `slipbox:link` (alias `persist`) | Accepted atoms get a fresh Folgezettel ID and enter `store/`, immutable forever. |
| — Retrieve | `slipbox:search` | Answer questions with claim-by-claim citations. |

### Higher-level workflows

| Workflow | Skill | What it does |
|----------|-------|--------------|
| Batch adapt | `slipbox:triage` | Sweep the whole inbox backlog end-to-end (the interactive form of the nightly auto-adapt job). |
| Re-extraction | `slipbox:readapt` | Re-read a source's archived original with a better model and propose *additional* atoms through review — the fidelity-ceiling escape hatch. |
| Topic-map upkeep | `slipbox:consolidate` | Split oversized topics and grow the bookmark hierarchy by the single-parameter-N rule, never dropping a note. |
| Admin oversight | `slipbox:oversight` | Surface domain drift from per-contributor scope statistics and guide charter-level decisions. |

## The tools — the deterministic surface the skills drive

Each skill is a conversation; the mechanical steps it takes are `slipbox_*` tools
that run in-process (`schemas.py` declares what the LLM sees, `tools.py` runs it).
A handler always returns a JSON string and never raises. Every tool is registered
into one toolset, `slipbox` (`__init__.TOOLSET`), and each skill declares
`requires_toolsets: [slipbox]` — so hermes won't start a flow unless the mechanics
are bound. That toolset name is the join key across all three faces of the set:
the `plugin.yaml` `provides_tools` manifest, the runtime binding in
`_active_schemas` / `tools.HANDLERS`, and the skills' `requires_toolsets`. The
tools fall in three groups (`__init__._active_schemas`):

**Read-only** — pure reads, `embeddings.db` opened read-only, zero commits:

| Tool | What it returns |
|------|-----------------|
| `slipbox_show` | A note rendered: frontmatter + body, wikilinks resolved, typed connections surfaced. |
| `slipbox_lookup` | The shared four-layer lookup as a *prefilter* — deduplicated candidates with provenance, over the `store` / `source` / `stage` spaces you pick. |
| `slipbox_inbox` · `slipbox_stage` · `slipbox_sources` · `slipbox_store` | List each space (stage/store take a status/prefix filter). |
| `slipbox_tree` · `slipbox_backlinks` | An ID's sequence neighbourhood; the notes whose wikilinks point at it. |
| `slipbox_index` | `index.md` parsed into a nested topic map with entry-note IDs. |
| `slipbox_original` | A source's archived original from cold storage — deliberate, quarantined, never embedded (powers `readapt`). |
| `slipbox_status` · `slipbox_log` · `slipbox_schedule` | Backlog counters + drift signal; a note's git history; cron/job/lock state. |

**Gated — the interactive read path** (`slipbox_search`, `slipbox_quote`): the
cited-summary channel. The whitepaper reserves it for when the models are
reachable, so these register **only when `SLIPBOX_SEMANTIC` is on**. `slipbox_search`
runs the lookup and frames the candidates for a claim-by-claim cited answer;
`slipbox_quote` returns a note's verbatim body.

**Writing — the CARP mechanics** — each takes the same `flock` as the scheduled
jobs and ends in a git commit:

| Stage | Tools |
|-------|-------|
| 0 Setup | `slipbox_setup` — create the layout, seed `index.md` / `SOUL.md`, init `embeddings.db`; idempotent, and fired automatically on the first session (`on_session_start`). |
| 1 Capture | `slipbox_capture` |
| 2 Adapt | `slipbox_source` · `slipbox_atom` · `slipbox_scope` · `slipbox_move_attachments` · `slipbox_archive_original` · `slipbox_drop_inbox` |
| 3 Review | `slipbox_review` |
| 4 Persist | `slipbox_persist` |
| Topic map | `slipbox_index_add` · `slipbox_index_write` |
| Housekeeping | `slipbox_purge_rejected` · `slipbox_reindex` |

The mechanical slash commands (`/slipbox-status`, `/slipbox-digest`, `/slipbox-inbox`,
`/slipbox-stage`, `/slipbox-store`, `/slipbox-show`, `/slipbox-accept`, `/slipbox-reject`,
`/slipbox-help`) and the `slipbox …` terminal CLI in `commands.py` cover the same
ground without an LLM in the loop.

## The store layout

```
slipbox-repo/
├── inbox/        # fleeting captures awaiting distillation   (+ .attachments/)
├── stage/        # distilled atoms awaiting review           (+ .attachments/)
├── store/        # atomic notes — immutable once placed      (+ .attachments/)
├── source/       # bibliography notes …                      (+ .attachments/  ← the originals, cold)
├── index.md      # nested topic map → entry notes
├── SOUL.md       # process & philosophy + domain charter
└── embeddings.db # sqlite-vec index (derived; outside git)
```

The self-describing `id` scheme tells a note's kind by its shape: a **store**
atom's `id` is a scalar Folgezettel position, a **stage** atom's `id` is the
*list* of placement candidates, a **source** note's `id` is a UUID.

## Retrieval — one shared four-layer mechanism

All lookup (search, source dedup, placement) runs the same steps, with
decorrelated failure modes — a query must defeat all four to miss:

1. **Structural** — `index.md` topics yield entry points at near-zero cost.
2. **Positional** — a reranker-scored bisection over the Folgezettel order sweeps
   whole threads, catching notes that share no vocabulary with the query.
3. **Semantic** — global k-NN over bge-m3 embeddings in sqlite-vec.
4. **Judged / lexical** — a reader ranks the union; grep over wikilinks answers
   backlinks. Vectors nominate; a reader decides.

## The three models (whitepaper §"Semantic layer")

| Role | Reference | Where it runs |
|------|-----------|---------------|
| embedder | `BAAI/bge-m3` (1024-dim dense) | in-process, CPU |
| reranker | `BAAI/bge-reranker-v2-m3` (cross-encoder) | in-process, CPU |
| judge | ~24B generalist | the host agent — or an in-process fallback when headless |

The GPU belongs to the conversational model; CARP runs on CPU, asynchronously.
The semantic layer **degrades gracefully** — with no models installed the store
still captures, adapts, reviews, persists and greps; lookup falls back to token
overlap and a brute-force cosine scan.

## Automation

Three scheduled jobs take `flock`-based locks shared with the manual skills:
`auto-adapt` (nightly distillation), the single-instance `persist` job
(`slipbox persist-accepted`), and the morning `digest` (`slipbox digest`).
`slipbox_schedule` reports their state.

## Layout of this package

```
slipbox/
├── __init__.py        register() — binds tools, commands, skills, hooks
├── config.py          repository layout + tunables (all env-overridable)
├── folgezettel.py     identifier parsing, ordering, allocation
├── notes.py           frontmatter, wikilinks, the self-describing id scheme
├── indexmd.py         the nested topic map (index.md)
├── embeddings.py      sqlite-vec store, three vector tables, freshness
├── models.py          in-process embedder / reranker / judge (lazy singletons)
├── lookup.py          the shared four-layer mechanism + dedup signal
├── operations.py      the CARP lifecycle — what the tools actually do
├── schemas.py         tool schemas (what the LLM sees)
├── tools.py           tool handlers ((args) -> JSON string, never raises)
├── commands.py        slash commands, the morning digest, the CLI
├── hooks.py           on_session_start (first-run setup + freshness) / on_session_end (commit)
├── cronspec.py        a tiny cron parser for slipbox_schedule
├── plugin.yaml        the hermes-agent manifest
├── requirements.txt   optional semantic-layer deps (embedder, reranker, judge)
├── templates/SOUL.md  the deployment SOUL seed (fill in the domain charter)
└── skills/            capture · adapt · review · link · search
                       · triage · readapt · consolidate · oversight
```

## Running

```bash
# The lifecycle needs nothing but Python + git:
python tests/run.py          # bare-interpreter test harness (or: pytest tests/)

# Terminal CLI (operates on $SLIPBOX_REPO):
python -m slipbox --help     # via the hermes CLI: `slipbox …`
```

Install into hermes as a plugin — hermes loads `plugin.yaml`, calls `register()`
and runs the tools, skills, hooks and scheduled jobs in-process:

```bash
# Local dev: symlink the package into the hermes plugins dir, then enable it.
ln -sfn "$PWD/slipbox" ~/.hermes/plugins/slipbox
hermes plugins enable slipbox
# (or from a git remote:  hermes plugins install <owner/repo> --enable)

# The semantic layer is optional; install its deps into hermes' environment:
pip install -r slipbox/requirements.txt
```

`setup-hermes-profile.sh` does all of this against a throwaway profile.

Key environment variables (all optional, sane defaults): `SLIPBOX_REPO`,
`SLIPBOX_DEVICE`, `SLIPBOX_SEMANTIC`, `SLIPBOX_EMBED_MODEL`, `SLIPBOX_RERANK_MODEL`,
`SLIPBOX_JUDGE_MODEL`, `SLIPBOX_WINDOW`, `SLIPBOX_PROBE_BUDGET`,
`SLIPBOX_DUPLICATE_DISTANCE`, `SLIPBOX_PENDING_WARN`, `SLIPBOX_CRON_*`. See
`config.py`.
