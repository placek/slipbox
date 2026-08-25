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
| 2 Adapt | `slipbox:adapt` | Handed to the **dedicated atomiser agent**, which distils in the background: one-idea atoms in `stage/`, each assigned its **proposed Folgezettel ID** (which also names the file) from the placement lookup; embed at once; scope-classify; cache placement; archive the original into cold storage. |
| 3 Review | `slipbox:review` | The human quality gate — accept/reject whole per-source batches; shape titles, placement, duplicates. |
| 4 Persist | `slipbox:link` (alias `persist`) | Accepted atoms are **moved** into `store/` under the ID assigned at adapt (a human override re-derives it), immutable forever; rejected ones are purged. |
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
| `slipbox_adapt_status` | Progress of the background distillation jobs, and whether the atomiser's model is reachable. |

**Gated — the interactive read path** (`slipbox_search`, `slipbox_quote`): the
cited-summary channel. The whitepaper reserves it for when the models are
reachable, so these register **only when `SLIPBOX_SEMANTIC` is on**. `slipbox_search`
runs the lookup and frames the candidates for a claim-by-claim cited answer;
`slipbox_quote` returns a note's verbatim body.

**Writing — the CARP mechanics** — each takes the same `flock` as the scheduled
jobs and ends in a git commit:

| Stage | Tools |
|-------|-------|
| 0 Setup | `slipbox_setup` — **`git init` the store**, create the layout, seed `index.md` / `SOUL.md`, init `embeddings.db`; idempotent, and fired automatically on the first session (`on_session_start`). A store nested inside a larger repository is left to commit into that one. |
| 1 Capture | `slipbox_capture` |
| 2 Adapt | `slipbox_adapt` · `slipbox_readapt` — hand distillation to the **dedicated atomiser agent** (returns a job id, never blocks). The mechanics it drives, also callable directly when the agent is off: `slipbox_source` · `slipbox_atom` · `slipbox_scope` · `slipbox_move_attachments` · `slipbox_archive_original` · `slipbox_drop_inbox` |
| 3 Review | `slipbox_review` |
| 4 Persist | `slipbox_persist` |
| Topic map | `slipbox_index_add` · `slipbox_index_write` |
| Housekeeping | `slipbox_purge_rejected` · `slipbox_reindex` |

The mechanical slash commands (`/slipbox-adapt`, `/slipbox-adapt-status`,
`/slipbox-status`, `/slipbox-digest`, `/slipbox-inbox`, `/slipbox-stage`,
`/slipbox-store`, `/slipbox-show`, `/slipbox-accept`, `/slipbox-reject`,
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
atom's `id` is a scalar Folgezettel position, a **stage** atom's `id` is its
proposed position in one-element *list* form (which also names the file, applied
verbatim at persist), a **source** note's `id` is a UUID.

## Retrieval — one shared four-layer mechanism

All lookup (search, source dedup, placement) runs the same steps, with
decorrelated failure modes — a query must defeat all four to miss:

1. **Structural** — `index.md` topics yield entry points at near-zero cost.
2. **Positional** — a reranker-scored bisection over the Folgezettel order sweeps
   whole threads, catching notes that share no vocabulary with the query.
3. **Semantic** — global k-NN over bge-m3 embeddings in sqlite-vec.
4. **Judged / lexical** — a reader ranks the union; grep over wikilinks answers
   backlinks. Vectors nominate; a reader decides.

Each lookup reports which models actually ran — `semantic_scorer` (the embedder)
and `positional_scorer` (the reranker, or the token-overlap fallback) — so you can
confirm the reranker engaged. The positional layer only fires when `index.md` has
a topic matching the query, so keep the topic map populated (`slipbox:consolidate`).
Topic matching counts **content words only** (`text.py`): a query sharing nothing
but "and" with a topic title used to nominate every note beneath it.

The union is ordered by vector distance, the one calibrated signal available.
A candidate the positional layer swept in but the embedder never scored takes a
neutral distance (`SLIPBOX_POSITIONAL_DISTANCE`), and agreement between the two
layers subtracts a small bonus (`SLIPBOX_BOTH_LAYERS_BONUS`) — a prior that
promotes a note past near-equals, never one that outranks a much closer match.

## The models (whitepaper §"Semantic layer")

| Role | Reference | Where it runs |
|------|-----------|---------------|
| embedder | `BAAI/bge-m3` (1024-dim dense) | in-process, CPU |
| reranker | `BAAI/bge-reranker-v2-m3` (cross-encoder) | in-process, CPU |
| atomiser | `Qwen/Qwen3-4B-Instruct-2507` (`atomizer.model`) | the **dedicated atomiser agent**, in-process |
| judge | ~24B generalist (`SLIPBOX_JUDGE_MODEL`) | a *reference*: the default any generative role falls back to |

Note what the judge is today: a configured **reference**, not a running role. No
code path invokes it on its own — `slipbox_search` nominates and frames, and the
cited summary is composed by the host agent. It becomes a loaded model only when
a deployment points `atomizer.model` at it, so `doctor` reports its name without
probing it.

The atomiser and the judge are deliberately *separate roles*. The judge reference
is a large generalist, which is right for a summary a human reads interactively
and wrong for an unattended batch: with no CUDA the work lands on CPU, where
decoding is bound by memory traffic, so a 24B spends tens of minutes per entry.
The atomiser therefore defaults to a small **instruct** (non-reasoning) model
picked for throughput and for reliably emitting one JSON object — a hybrid
reasoning model would spend the budget on a `<think>` block nobody parses.
Loading is device-aware: NF4 on CUDA, **bf16 on CPU where AVX512-BF16 exists**,
float32 otherwise. 4-bit is a GPU technique; on CPU it needs an optional kernels
package and, lacking it, is slower than not quantising while still costing
accuracy.

The GPU belongs to the conversational model; CARP runs on CPU, asynchronously.
The semantic layer **degrades gracefully** — with no models installed the store
still captures, adapts, reviews, persists and greps; lookup falls back to token
overlap and a brute-force cosine scan.

## The atomiser — distillation is a dedicated agent

Atomisation does **not** run on the host conversational agent. It runs as a
dedicated agent with its own model, its own instructions and its own clean
context, off the conversation — because distilling inline blocks the chat, lets
whatever was said earlier leak into what the store means, and makes an
unattended nightly run impossible.

Every trigger routes through it, manual or scheduled: `slipbox_adapt`,
`/slipbox-adapt`, `slipbox:adapt`, `slipbox:triage`, `slipbox:readapt`
(re-reading an archived original) and the `auto-adapt` cron job
(`slipbox adapt`). The call returns a **job id immediately**; the atoms appear in
`stage/` as the agent commits them, and `slipbox_adapt_status` reports progress.

The agent only ever *proposes*: it returns a JSON plan which is validated and
bounded before the deterministic operations execute it, and a human still
reviews every atom. A hallucinated link target costs that one atom, not the
distillation.

Configured from **plugin configuration** (`plugins.entries.slipbox.atomizer.*`
in hermes' `config.yaml`), falling back to `SLIPBOX_ATOMIZER_*` env vars, then to
shipped defaults:

| Setting | Default | What it does |
|---------|---------|--------------|
| `enabled` | `true` | Off restores hand-distillation by the host agent. |
| `backend` | `local` | `local` = the in-process judge, nothing leaves the machine. `host` = hermes' own LLM lane (`ctx.llm`), which needs `plugins.entries.slipbox.llm.allow_model_override` to steer the model. |
| `model` | the judge model | Which model distils. |
| `instructions` | `templates/ATOMIZER.md` | The composition contract. Inline text or a path. Overriding it deliberately changes what the store *means*. |
| `max_atoms` | `12` | Ceiling per entry — better three sharp notes than ten restatements. |
| `candidates` | `12` | Related store notes shown as placement candidates. |
| `max_chars` / `max_tokens` | `24000` / `2048` | Input and generation bounds. `max_tokens` is kept *reachable within* `timeout` — see below. |
| `temperature` | `0.1` | Near-greedy: faithfulness, not invention. |
| `timeout` | `1800` | Hard wall-clock bound on one distillation, retries included. |
| `retries` | `2` | Re-asks when the returned JSON is unusable (shares the one budget). |

```yaml
# ~/.hermes/config.yaml
plugins:
  entries:
    slipbox:
      atomizer:
        backend: local
        model: mistralai/Mistral-Small-Instruct-2409
        instructions: /path/to/my-contract.md   # or inline text
```

`slipbox doctor` reports whether the configured model is reachable — with a
*cheap* probe that never pulls the weights.

**Speed, and why background is not a nicety.** With `backend: local` the judge
runs on **CPU**: the GPU belongs to the conversational model, so a 4-bit 24B is
placed on CPU by `device_map="auto"` whenever VRAM is already spoken for.
Measured here: ~80 s to load, then **~1.5 tok/s**. One entry is therefore
minutes-to-tens-of-minutes, which is exactly the whitepaper's stated operating
point ("asynchronous and low-priority, so a nightly batch tolerates single-digit
tokens per second") — and exactly why every trigger hands off to a job instead
of blocking.

`atomizer.timeout` (default 1800 s) is a **real wall-clock bound**, enforced with
`MaxTimeCriteria` on the local path and covering the whole proposal including
retries — a token ceiling is not a time ceiling at 1.5 tok/s. If distillation
keeps timing out, the levers are: lower `max_tokens` / `max_chars`, raise
`timeout`, pick a smaller local model, or set `backend: host` to borrow the
model hermes already has loaded (fast, but the content leaves the machine).

## Automation

Four scheduled jobs take `flock`-based locks shared with the manual skills:
`auto-adapt` (nightly distillation by the atomiser agent, `slipbox adapt`), the
single-instance `persist` job (`slipbox persist-accepted`), the morning `digest`
(`slipbox digest`), and `reindex`. `slipbox_schedule` reports their state. The
atomiser holds its own lock name, so a manual distillation and the cron sweep
serialise instead of racing.

## Multiple knowledge bases (one instance, several repos)

Set `SLIPBOX_REPOS="work=/kb/work,personal=/kb/personal"` and one plugin instance
serves several stores at once. Every tool takes an optional `repo` argument
selecting which base it acts on; **the first configured entry is the default**
when a call names none, and each result echoes its `repo`. The stores are fully
isolated — separate `embeddings.db`, locks, `index.md` and `SOUL.md` charter — so
they can be adapted and persisted concurrently. First-run setup, the scheduled
jobs and the session hooks loop over every configured repo (`slipbox <job> --repo
<name>` targets one). With `SLIPBOX_REPOS` unset the plugin stays single-repo
(`$SLIPBOX_REPO`), unchanged.

### Read-only agents

Set `SLIPBOX_READONLY=1` (per profile) and the plugin exposes only its **read
surface** — the read-only tools plus the `slipbox_search` / `slipbox_quote` read
path and the `search` skill. Every write tool and write skill is withheld, the
write CLI subcommands are refused, and the setup/commit hooks are disabled (the
freshness report still runs). So one agent can query a store it must not modify
while another instance — or the scheduled jobs — does the writing.

## Layout of this package

```
slipbox/
├── __init__.py        register() — binds tools, commands, skills, hooks
├── config.py          repository layout + tunables (all env-overridable)
├── folgezettel.py     identifier parsing, ordering, allocation
├── notes.py           frontmatter, wikilinks, the self-describing id scheme
├── indexmd.py         the nested topic map (index.md)
├── text.py            query tokenisation shared by the structural/lexical layers
├── embeddings.py      sqlite-vec store, three vector tables, freshness
├── models.py          in-process embedder / reranker / judge (lazy singletons)
├── atomizer.py        the dedicated distillation agent + its background jobs
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
├── templates/ATOMIZER.md  the atomiser's default instructions (the contract)
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

# The semantic layer is optional. Its deps (torch, FlagEmbedding) are heavy and
# usually cannot go into hermes' own read-only environment, so build a venv from
# hermes' OWN interpreter and point the plugin at it:
"$(dirname "$(readlink -f "$(command -v hermes)")")/python3" -m venv .venv-semantic
.venv-semantic/bin/pip install -r slipbox/requirements.txt
export SLIPBOX_SEMANTIC_VENV="$PWD/.venv-semantic"   # or set it in the profile .env
```

The plugin **mounts that venv itself**, prepending it to `sys.path` (prepending
matters: the host ships a newer `huggingface_hub` that would otherwise shadow the
venv's and break `transformers`). Where a foreign-built torch also needs
`libstdc++` that the dynamic linker cannot find — NixOS, typically — the plugin
`dlopen`s it with `RTLD_GLOBAL` from `SLIPBOX_NATIVE_LIBS` (default: the nix-ld
directory), which is the one way to fix that from *inside* a running process:
`LD_LIBRARY_PATH` is read at exec, long before any Python runs.

The upshot is that a bare `hermes` works from any directory, with no launcher
wrapper and no `PYTHONPATH`/`LD_LIBRARY_PATH`. Rebuild the venv after a hermes
upgrade — its interpreter path changes. `slipbox doctor` reports what resolved.

`setup-hermes-profile.sh` does all of this against a throwaway profile.

Key environment variables (all optional, sane defaults): `SLIPBOX_REPO`,
`SLIPBOX_REPOS` (multi-repo; the first entry is the default), `SLIPBOX_READONLY`,
`SLIPBOX_SEMANTIC_VENV`, `SLIPBOX_NATIVE_LIBS`, `SLIPBOX_ATOMIZER_*` (the
dedicated atomiser: backend, model, instructions, bounds),
`SLIPBOX_DEVICE`, `SLIPBOX_SEMANTIC`, `SLIPBOX_EMBED_MODEL`, `SLIPBOX_RERANK_MODEL`,
`SLIPBOX_JUDGE_MODEL`, `SLIPBOX_WINDOW`, `SLIPBOX_PROBE_BUDGET`,
`SLIPBOX_DUPLICATE_DISTANCE`, `SLIPBOX_POSITIONAL_DISTANCE`,
`SLIPBOX_BOTH_LAYERS_BONUS`, `SLIPBOX_PENDING_WARN`, `SLIPBOX_CRON_*`,
`SLIPBOX_GIT_NAME` / `SLIPBOX_GIT_EMAIL` (the author of last resort for the
repository first-run setup creates). See `config.py`.

`SLIPBOX_DEVICE` steers both the embedder/reranker precision *and* where a
generative model is placed — pin it to `cpu` to keep the GPU for the
conversational model.

**Where the store goes when nothing says.** `SLIPBOX_REPO` (or `SLIPBOX_REPOS`)
decides it; failing that, the parent of the plugin package — the `<repo>/slipbox`
deployment, where the plugin is installed *into* the knowledge base. The one
exception is that the plugin's **own source checkout is never adopted as a
store**: `Path(__file__).resolve()` follows symlinks, so under the dev install
(`ln -sfn "$PWD/slipbox" …`) that parent is the git checkout, and an
unconfigured instance would create `inbox/`, `store/` and `embeddings.db` there
and commit notes into the project's history. It falls back to
`$XDG_DATA_HOME/slipbox` (default `~/.local/share/slipbox`) instead.
`slipbox doctor` reports the resolved `root` and the `root_origin` that chose it,
so this is never something to infer.
