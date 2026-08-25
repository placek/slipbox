"""Tests for the slipbox plugin — no models required.

The whole semantic layer degrades gracefully (`embeddings.EmbeddingError` when
bge-m3 is absent), so the CARP lifecycle, the identifier algebra and the topic
map are all exercised on a bare interpreter. Run with `pytest`.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slipbox import config, folgezettel as fz, indexmd, notes, operations as ops  # noqa: E402


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    root = tmp_path / "kb"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "tester"], check=True)
    monkeypatch.setenv("SLIPBOX_REPO", str(root))
    return root


# --- Folgezettel identifiers -------------------------------------------------

def test_order_is_segmentwise_not_lexicographic():
    ids = ["21", "21-a-10", "21-a-2", "22", "21-a", "3"]
    assert fz.order(ids) == ["3", "21", "21-a", "21-a-2", "21-a-10", "22"]


def test_allocate_after_descends_and_increments():
    known = {"21", "21-a"}
    assert fz.allocate_after("21", known) == "21-b"     # sibling of 21-a
    assert fz.allocate_after("21-a", known) == "21-a-1"  # continue below 21-a
    assert fz.allocate_after(None, known) == "22"        # new top-level thread


def test_letters_continue_bijectively_past_z():
    assert fz.next_letter("z") == "aa"
    assert fz.next_letter("az") == "ba"


# --- The self-describing id scheme -------------------------------------------

def test_source_id_is_a_uuid(repo):
    result = ops.create_source("Deep Work", author="Newport", source_type="book", root=repo)
    assert len(result["id"]) == 36 and result["id"].count("-") == 4


def test_stage_id_is_the_proposed_id_as_a_list(repo):
    # First atom of an empty store proposes the first top-level thread, "1".
    result = ops.create_atom("A claim", "A body.", candidates=["new-thread"], root=repo)
    assert result["proposed_id"] == "1"
    note = notes.load(repo, result["path"])
    assert isinstance(note.frontmatter["id"], list)   # list = still in stage
    assert note.frontmatter["id"] == ["1"]            # ...holding its proposed ID
    assert note.frontmatter["link_after"] == ""       # human override slot, empty


def test_store_id_is_scalar_folgezettel(repo):
    atom = ops.create_atom("A claim", "A body.", candidates=["new-thread"], root=repo)
    ops.review(atom["path"], config.REVIEW_ACCEPTED, root=repo)
    placed = ops.persist(atom["path"], new_thread=True, topic=["Topic"], root=repo)
    assert placed["id"] == "1"
    note = notes.load(repo, placed["path"])
    assert note.frontmatter["id"] == "1" and isinstance(note.frontmatter["id"], str)


# --- The refined workflow: proposed IDs + rich sources -----------------------

def test_batch_atoms_get_distinct_proposed_ids(repo):
    # An empty store: three atoms of one thread must not collide on their IDs.
    a = ops.create_atom("Head", "b", candidates=["new-thread"], root=repo)
    assert a["proposed_id"] == "1"
    b = ops.create_atom("Continues the thread", "b", link_after="1", root=repo)
    assert b["proposed_id"] == "1-a"      # descends below its predecessor
    c = ops.create_atom("A second continuation", "b", link_after="1", root=repo)
    assert c["proposed_id"] == "1-b"      # sibling, not a re-used slot
    d = ops.create_atom("An independent thought", "b", candidates=["new-thread"], root=repo)
    assert d["proposed_id"] == "2"        # a fresh top-level thread


def test_persist_applies_the_proposed_id_verbatim(repo):
    atom = ops.create_atom("Head", "b", candidates=["new-thread"], root=repo)
    ops.persist(atom["path"], root=repo)          # no after/new_thread — use proposal
    assert notes.load(repo, "store/1.md") is not None
    follow = ops.create_atom("Below", "b", link_after="1", root=repo)
    assert follow["proposed_id"] == "1-a"
    placed = ops.persist(follow["path"], root=repo)
    assert placed["id"] == "1-a" and "proposed" in placed["placement"]


def test_human_link_after_overrides_the_proposal(repo):
    ops.persist(ops.create_atom("Head", "b", candidates=["new-thread"], root=repo)["path"],
                root=repo)  # store/1
    atom = ops.create_atom("New thread guess", "b", candidates=["new-thread"], root=repo)
    assert atom["proposed_id"] == "2"
    # The reviewer decides it actually continues note 1 instead.
    ops.review(atom["path"], config.REVIEW_ACCEPTED, link_after="1", root=repo)
    placed = ops.persist(atom["path"], root=repo)
    assert placed["id"] == "1-a" and placed["after"] == "1"


def test_source_carries_rich_metadata_and_moves_media(repo, tmp_path):
    media = tmp_path / "figure.png"
    media.write_bytes(b"\x89PNG fake")
    cap = ops.capture("Paper", "text of the paper", attachments=[str(media)], root=repo)
    assert notes.attachments_of(notes.load(repo, cap["path"])) == ["figure.png"]
    src = ops.create_source(
        "Attention Is All You Need", author="Vaswani et al.", source_type="article",
        reference="arXiv:1706.03762", description="Transformers replace recurrence.",
        date="2017-06-12", topic="sequence transduction",
        tags=["transformers", "attention"], attachments=["figure.png"], root=repo)
    note = notes.load(repo, src["path"])
    assert note.frontmatter["date"] == "2017-06-12"
    assert note.frontmatter["topic"] == "sequence transduction"
    assert note.frontmatter["tags"] == ["transformers", "attention"]
    # media moved out of the inbox and into the source's cold store, linked here
    assert not (repo / config.INBOX_ATTACHMENTS / "figure.png").exists()
    assert (repo / config.SOURCE_ATTACHMENTS / note.stem / "figure.png").exists()
    assert note.frontmatter["attachments"] == [f"{config.SOURCE_ATTACHMENTS}/{note.stem}/figure.png"]


# --- The CARP lifecycle ------------------------------------------------------

def test_full_carp_roundtrip(repo):
    cap = ops.capture("Focus", "Newport on focus.", captured_by="alice", root=repo)
    assert ops.inbox(repo)["count"] == 1
    assert cap["commit"]["committed"]

    src = ops.create_source("Deep Work", author="Newport", source_type="book", root=repo)
    atom = ops.create_atom(
        "Focused work is scarce and therefore valuable",
        f"Distraction is the default, so focus is scarce. source [[{src['path'][:-3]}]]",
        source=[f"[[{src['path'][:-3]}]]"], candidates=["new-thread"],
        new_thread_topic="Attention", scope="in", root=repo,
    )
    ops.review(atom["path"], config.REVIEW_ACCEPTED, decided_by="ed", root=repo)
    placed = ops.persist(atom["path"], new_thread=True, topic=["Attention"], root=repo)
    assert placed["id"] == "1"
    assert ops.store(root=repo)["count"] == 1

    # A follow-on atom descends below the first.
    a2 = ops.create_atom("Attention residue lingers", "Switching leaves residue.",
                         candidates=["1"], link_after="1", root=repo)
    ops.review(a2["path"], config.REVIEW_ACCEPTED, link_after="1", root=repo)
    p2 = ops.persist(a2["path"], root=repo)
    assert p2["id"] == "1-a" and p2["after"] == "1"


def test_archive_original_goes_to_source_attachments(repo):
    cap = ops.capture("Focus", "raw text", captured_by="alice", root=repo)
    src = ops.create_source("Deep Work", author="Newport", source_type="book", root=repo)
    result = ops.archive_original(cap["path"], src["path"][:-3], root=repo)
    assert result["original"].startswith(f"{config.SOURCE_ATTACHMENTS}/")
    assert not (repo / cap["path"]).exists()           # moved, not copied
    src_note = notes.load(repo, src["path"])
    assert src_note.frontmatter["original"] == result["original"]


def test_rejected_atom_is_purged_not_the_original(repo):
    atom = ops.create_atom("A claim", "A body.", candidates=["new-thread"], root=repo)
    ops.review(atom["path"], config.REVIEW_REJECTED, root=repo)
    purged = ops.purge_rejected(repo)
    assert atom["path"] in purged["removed"]
    assert ops.stage(root=repo)["count"] == 0


def test_immutability_persist_refuses_to_overwrite(repo):
    a1 = ops.create_atom("One", "b", candidates=["new-thread"], root=repo)
    ops.review(a1["path"], config.REVIEW_ACCEPTED, root=repo)
    ops.persist(a1["path"], new_thread=True, root=repo)  # → store/1.md
    # A hand-forged collision would raise; allocate always finds a free slot,
    # so a second new thread becomes 2, never 1.
    a2 = ops.create_atom("Two", "b", candidates=["new-thread"], root=repo)
    ops.review(a2["path"], config.REVIEW_ACCEPTED, root=repo)
    assert ops.persist(a2["path"], new_thread=True, root=repo)["id"] == "2"


# --- Scope classification ----------------------------------------------------

def test_scope_shows_in_status_and_per_contributor(repo):
    atom = ops.create_atom("A claim", "b", candidates=["new-thread"],
                          scope="adjacent", captured_by="bob", root=repo)
    st = ops.status(repo)
    assert st["stage"]["by_scope"]["adjacent"] == 1
    assert st["stage"]["scope_by_contributor"]["bob"]["adjacent"] == 1
    # set_scope mutates it
    ops.set_scope(atom["path"], "out", "off topic", root=repo)
    assert ops.stage(root=repo)["entries"][0]["scope"] == "out"


# --- Backpressure ------------------------------------------------------------

def test_capture_warns_when_review_queue_is_long(repo, monkeypatch):
    monkeypatch.setenv("SLIPBOX_PENDING_WARN", "1")
    ops.create_atom("pending", "b", candidates=["new-thread"], root=repo)
    result = ops.capture("More", "raw", root=repo)
    assert result["warning"] and "backlog" in result["warning"]


# --- Originals layer (readapt) -----------------------------------------------

def test_original_reads_archived_extraction(repo):
    cap = ops.capture("Focus", "the full extracted text of the source", root=repo)
    src = ops.create_source("Deep Work", author="Newport", source_type="book", root=repo)
    ops.archive_original(cap["path"], src["path"][:-3], root=repo)
    result = ops.original(src["path"][:-3], root=repo)
    assert result["count"] == 1
    assert "full extracted text" in result["files"][0]["text"]


def test_original_refuses_a_source_without_one(repo):
    src = ops.create_source("Unarchived", author="X", source_type="web", root=repo)
    with pytest.raises(ops.OperationError):
        ops.original(src["path"][:-3], root=repo)


# --- Consolidation (index_write) ---------------------------------------------

def test_index_write_replaces_and_guards_dropped_refs(repo):
    (repo / config.STORE).mkdir(parents=True, exist_ok=True)
    for i in ("1", "2"):
        (repo / config.STORE / f"{i}.md").write_text(f"---\nid: {i}\ntitle: T{i}\n---\n\nb\n")
    ops.index_add(["Attention"], "1", root=repo)
    ops.index_add(["Attention"], "2", root=repo)
    # A rewrite that keeps both references reports no drop.
    kept = ops.index_write("# Index\n\n- Attention\n  - Deep work — [[1]] · [[2]]\n", root=repo)
    assert kept["dropped"] == [] and kept["bookmarks"] == 2
    # A rewrite that loses a reference is flagged.
    lost = ops.index_write("# Index\n\n- Attention — [[1]]\n", root=repo)
    assert lost["dropped"] == ["2"] and lost["warning"]


# --- Topic map ---------------------------------------------------------------

def test_index_add_creates_nested_topics(repo):
    (repo / config.STORE).mkdir(parents=True, exist_ok=True)
    (repo / config.STORE / "1.md").write_text("---\nid: 1\ntitle: T\n---\n\nbody\n")
    ops.index_add(["Attention", "Deep work"], "1", root=repo)
    entries = indexmd.parse(repo)
    paths = [e["path"] for e in entries]
    assert ["Attention"] in paths
    assert ["Attention", "Deep work"] in paths
    assert indexmd.entry_ids(repo, "deep work attention") == ["1"]


# --- Frontmatter round-trip --------------------------------------------------

def test_nested_frontmatter_roundtrips_as_json():
    fm = {"id": ["21-a", "new-thread"], "review": {"status": "pending"}, "title": "x"}
    text = notes.compose(fm, "body")
    parsed, body = notes.parse_frontmatter(text)
    assert parsed["id"] == ["21-a", "new-thread"]
    assert parsed["review"] == {"status": "pending"}
    assert body.strip() == "body"


# --- Multiple repositories (named knowledge bases) ---------------------------

def _init_git(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "tester"], check=True)


def test_repo_registry_default_and_resolution(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv("SLIPBOX_REPOS", f"work={a},personal={b}")
    assert list(config.repos()) == ["work", "personal"]     # order preserved
    assert config.default_repo() == "work"                  # first = default
    assert config.repo_root(None) == a.resolve()            # no name → default
    assert config.repo_root("personal") == b.resolve()
    with pytest.raises(KeyError):
        config.repo_root("nope")


def test_tools_route_and_isolate_by_repo(tmp_path, monkeypatch):
    import json

    from slipbox import tools

    a, b = tmp_path / "a", tmp_path / "b"
    _init_git(a)
    _init_git(b)
    monkeypatch.setenv("SLIPBOX_REPOS", f"work={a},personal={b}")
    monkeypatch.delenv("SLIPBOX_REPO", raising=False)

    # setup with no repo initializes EVERY configured repo
    setup = json.loads(tools.slipbox_setup({}))
    assert {r["repo"] for r in setup["setup"]} == {"work", "personal"}
    assert (a / "index.md").is_file() and (b / "index.md").is_file()

    # capture routes to the named repo, and each result echoes its repo
    tools.slipbox_capture({"repo": "work", "title": "W", "content": "in work"})
    tools.slipbox_capture({"repo": "personal", "title": "P", "content": "in personal"})

    inbox_w = json.loads(tools.slipbox_inbox({"repo": "work"}))
    inbox_p = json.loads(tools.slipbox_inbox({"repo": "personal"}))
    assert inbox_w["repo"] == "work" and inbox_p["repo"] == "personal"
    assert [e["title"] for e in inbox_w["entries"]] == ["W"]       # isolation:
    assert [e["title"] for e in inbox_p["entries"]] == ["P"]       # no cross-leak

    # omitting repo falls back to the default (first = work)
    default_inbox = json.loads(tools.slipbox_inbox({}))
    assert default_inbox["repo"] == "work"
    assert [e["title"] for e in default_inbox["entries"]] == ["W"]

    # an unknown repo is a clean error, never a crash
    assert "error" in json.loads(tools.slipbox_inbox({"repo": "ghost"}))


def test_readonly_registers_only_the_read_surface(monkeypatch):
    import slipbox

    monkeypatch.setenv("SLIPBOX_READONLY", "1")
    names = {s["name"] for s in slipbox._active_schemas()}
    # reads + the search/quote read path are present…
    assert {"slipbox_show", "slipbox_lookup", "slipbox_search", "slipbox_quote"} <= names
    # …and every write tool is withheld.
    for w in ("slipbox_capture", "slipbox_atom", "slipbox_persist", "slipbox_reindex", "slipbox_setup"):
        assert w not in names
    # `search` is the only read-only skill.
    assert slipbox.READONLY_SKILLS == ("search",)

    monkeypatch.delenv("SLIPBOX_READONLY")
    assert "slipbox_capture" in {s["name"] for s in slipbox._active_schemas()}


# --- The dedicated atomiser agent --------------------------------------------
#
# The agent's *model* is stubbed throughout: what these tests pin down is the
# contract around it — that a plan is validated before it touches the store,
# that the store is never trusted to the model, and that every trigger routes
# through the one background path.

def _plan_json(atoms):
    import json
    return json.dumps({
        "source": {"title": "On Rivers", "author": "H", "type": "article",
                   "reference": "", "date": "", "topic": "water",
                   "tags": ["flow"], "summary": "A short literature note."},
        "atoms": atoms,
    })


def _atom(title, **over):
    base = {"title": title, "body": f"{title}. A full self-contained sentence.",
            "variants": [f"{title} (alt)"], "scope": "in",
            "scope_rationale": "squarely in", "link_after": None,
            "continues": None, "new_thread": True, "new_thread_topic": "water",
            "rationale": "opens a thread"}
    base.update(over)
    return base


def test_atomizer_plan_is_validated_before_it_touches_the_store():
    from slipbox import atomizer

    # An atom missing a body is not an atom — dropped, not written.
    plan = atomizer.parse_plan(_plan_json([
        _atom("Kept"), {"title": "No body", "body": "  "}, "not-an-object",
    ]))
    assert [a["title"] for a in plan["atoms"]] == ["Kept"]

    # A source block with no title is a failed distillation, never a guess.
    import json
    with pytest.raises(atomizer.AtomizerError):
        atomizer.parse_plan(json.dumps({"source": {"summary": "x"}, "atoms": []}))

    # Re-adaptation reuses the existing source, so it needs no source block.
    reused = atomizer.parse_plan(json.dumps({"atoms": [_atom("Extra")]}),
                                 require_source=False)
    assert [a["title"] for a in reused["atoms"]] == ["Extra"]


def test_atomizer_tolerates_fenced_and_prefixed_json():
    from slipbox import atomizer

    fenced = "Here is the plan:\n```json\n" + _plan_json([_atom("A")]) + "\n```\n"
    assert atomizer.parse_plan(fenced)["source"]["title"] == "On Rivers"


def test_atomizer_bounds_what_the_model_may_do(monkeypatch):
    from slipbox import atomizer

    monkeypatch.setenv("SLIPBOX_ATOMIZER_MAX_ATOMS", "2")
    plan = atomizer.parse_plan(_plan_json([_atom(f"A{i}") for i in range(9)]))
    assert len(plan["atoms"]) == 2                      # the ceiling is enforced

    # `continues` may only point BACKWARDS inside the batch — a forward or
    # self-reference would make chaining unresolvable, so it is dropped.
    plan = atomizer.parse_plan(_plan_json([
        _atom("first", continues=0), _atom("second", continues=0),
    ]))
    assert plan["atoms"][0]["continues"] is None
    assert plan["atoms"][1]["continues"] == 0

    # An unknown scope degrades to unclassified rather than corrupting metadata.
    plan = atomizer.parse_plan(_plan_json([_atom("s", scope="sideways")]))
    assert plan["atoms"][0]["scope"] is None


def test_atomizer_distils_an_entry_end_to_end(repo, monkeypatch):
    from slipbox import atomizer

    ops.setup(repo)
    ops.capture("Rivers", "Water flows downhill. It carries silt.", root=repo)
    entry = ops.inbox(repo)["entries"][0]["path"]

    # Stub the model: the agent's plan chains atom 1 onto atom 0.
    monkeypatch.setattr(atomizer, "_generate", lambda i, p: _plan_json([
        _atom("Water flows downhill"),
        _atom("Flow carries silt", continues=0, new_thread=False),
    ]))

    result = atomizer.distil(entry, repo)

    # The source note was written and both atoms cite it.
    assert result["source"]["title"] == "On Rivers"
    assert result["atom_count"] == 2
    ids = [a["proposed_id"] for a in result["atoms"]]
    assert ids == ["1", "1-a"]          # the second continues the first

    # Atoms are staged, never placed: the store is still empty.
    assert ops.stage(root=repo)["count"] == 2
    assert ops.store(root=repo)["count"] == 0

    # The original went to cold storage, so the inbox is clear.
    assert ops.inbox(repo)["count"] == 0
    assert result["archived"]


def test_atomizer_survives_one_bad_atom(repo, monkeypatch):
    from slipbox import atomizer

    ops.setup(repo)
    ops.capture("Rivers", "Water flows downhill.", root=repo)
    entry = ops.inbox(repo)["entries"][0]["path"]

    # The model hallucinates a link target that does not exist. That atom must
    # cost only itself — the rest of the distillation still lands.
    monkeypatch.setattr(atomizer, "_generate", lambda i, p: _plan_json([
        _atom("Good one"),
        _atom("Bad placement", link_after="not-an-id", new_thread=False),
    ]))

    result = atomizer.distil(entry, repo)
    assert result["atom_count"] == 1
    assert len(result["failed"]) == 1
    assert ops.stage(root=repo)["count"] == 1


def test_adapt_tool_hands_off_and_never_blocks(repo, monkeypatch):
    import json

    from slipbox import atomizer, tools

    ops.setup(repo)
    ops.capture("Rivers", "Water flows downhill.", root=repo)

    submitted = {}
    monkeypatch.setattr(atomizer, "submit",
                        lambda entries, root, repo=None: submitted.update(
                            entries=entries) or {"job": "adapt-test",
                                                 "status": "queued",
                                                 "entries": entries,
                                                 "backend": "local",
                                                 "model": "m"})

    # No `idents` → the whole usable inbox, and the call returns a job at once.
    result = json.loads(tools.slipbox_adapt({}))
    assert result["job"] == "adapt-test" and result["status"] == "queued"
    assert len(submitted["entries"]) == 1

    # Disabled, the tool refuses rather than silently letting the host distil.
    monkeypatch.setenv("SLIPBOX_ATOMIZER", "0")
    assert "error" in json.loads(tools.slipbox_adapt({}))


def test_atomizer_backend_status_never_loads_the_model(monkeypatch):
    from slipbox import atomizer, models

    # A status probe that pulled 24B of weights would be unusable in `doctor`.
    monkeypatch.setattr(models, "judge",
                        lambda: pytest.fail("status must not load the judge"))
    monkeypatch.setattr(models, "judge_importable", lambda: True)
    status = atomizer.backend_status()
    assert status["backend"] == "local" and status["reachable"] is True


def test_atomizer_reads_model_and_instructions_from_plugin_config(monkeypatch):
    from slipbox import config as cfg

    # Plugin config outranks the environment; the environment outranks defaults.
    monkeypatch.setattr(cfg, "_PLUGIN_CONFIG", None)
    monkeypatch.setenv("SLIPBOX_ATOMIZER_MODEL", "from-env")
    assert cfg.atomizer_model() == "from-env"

    monkeypatch.setattr(cfg, "_PLUGIN_CONFIG",
                        {"atomizer": {"model": "from-plugin-config",
                                      "instructions": "Do it this way.",
                                      "backend": "host", "max_atoms": 3}})
    assert cfg.atomizer_model() == "from-plugin-config"
    assert cfg.atomizer_instructions() == "Do it this way."
    assert cfg.atomizer_backend() == "host"
    assert cfg.atomizer_max_atoms() == 3

    # An unknown backend falls back to `local` rather than failing at call time.
    monkeypatch.setattr(cfg, "_PLUGIN_CONFIG", {"atomizer": {"backend": "wat"}})
    assert cfg.atomizer_backend() == "local"

    # With nothing configured the shipped contract is used, not an empty prompt.
    monkeypatch.setattr(cfg, "_PLUGIN_CONFIG", {})
    monkeypatch.delenv("SLIPBOX_ATOMIZER_MODEL", raising=False)
    assert "exactly one idea" in cfg.atomizer_instructions()


def test_semantic_venv_is_mounted_ahead_of_the_host(tmp_path, monkeypatch):
    import sys

    from slipbox import config as cfg, models

    site = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    monkeypatch.setenv("SLIPBOX_SEMANTIC_VENV", str(tmp_path / "venv"))
    monkeypatch.setattr(cfg, "_PLUGIN_CONFIG", {})
    monkeypatch.setattr(models, "_VENV_MOUNTED", False)
    # The C++ preload is a separate concern; keep this test to the path logic.
    monkeypatch.setattr(models, "_ensure_cxx_runtime", lambda: None)

    before = list(sys.path)
    try:
        mounted = models.mount_semantic_venv()
        # PREPENDED, not appended: the host ships its own, newer huggingface_hub,
        # and on an append that one shadows the venv's — leaving transformers
        # refusing to import against a version it does not support.
        assert mounted == str(site)
        assert sys.path[0] == str(site)
        # Idempotent — a second call must not stack duplicates.
        monkeypatch.setattr(models, "_VENV_MOUNTED", False)
        models.mount_semantic_venv()
        assert sys.path.count(str(site)) == 1
    finally:
        sys.path[:] = before

    # An unset or missing venv is simply "nothing to mount", never an error.
    monkeypatch.delenv("SLIPBOX_SEMANTIC_VENV")
    assert cfg.semantic_venv() is None
    monkeypatch.setenv("SLIPBOX_SEMANTIC_VENV", str(tmp_path / "nope"))
    assert cfg.semantic_venv() is None


def test_atomizer_refuses_to_queue_what_it_cannot_run(repo, monkeypatch):
    import json

    from slipbox import atomizer, tools

    ops.setup(repo)
    ops.capture("Rivers", "Water flows downhill.", root=repo)

    # Accepting a job and only failing in the background thread is the worst
    # outcome: the caller is told distillation started, and nothing says
    # otherwise until a later status check. Refuse at submit instead.
    monkeypatch.setattr(atomizer, "backend_status", lambda: {
        "enabled": True, "backend": "local", "model": "m",
        "reachable": False, "resident": False, "detail": "no torch here",
    })
    result = json.loads(tools.slipbox_adapt({}))
    assert "error" in result and "not reachable" in result["error"]
    assert atomizer.jobs() == [] or all(j.get("status") != "queued"
                                       for j in atomizer.jobs())


def test_atomizer_timeout_bounds_the_whole_proposal(repo, monkeypatch):
    import time

    from slipbox import atomizer

    # A local judge that runs out of time returns truncated JSON. Retrying that
    # with a fresh full budget turns one slow distillation into several, so the
    # timeout has to bound the proposal end to end, not each attempt.
    monkeypatch.setenv("SLIPBOX_ATOMIZER_TIMEOUT", "0")
    monkeypatch.setenv("SLIPBOX_ATOMIZER_RETRIES", "5")
    calls = []
    monkeypatch.setattr(atomizer, "_generate",
                        lambda i, p: calls.append(1) or "not json at all")

    started = time.monotonic()
    with pytest.raises(atomizer.AtomizerError):
        atomizer.propose({"title": "t", "content": "c", "truncated": False,
                          "charter": "", "topic_map": "", "related": [],
                          "attachments": [], "captured": "", "reference": "",
                          "already": [], "guidance": ""})
    assert len(calls) == 1                       # not 6: the budget stopped it
    assert time.monotonic() - started < 5


def test_local_backend_uses_the_atomiser_model_not_the_judge(monkeypatch):
    from slipbox import atomizer, config as cfg, models

    # The atomiser and the judge are different roles with different models. The
    # local path used to call judge_generate() with no name, which loaded the
    # JUDGE — so configuring atomizer.model changed only what status reported.
    monkeypatch.setattr(cfg, "_PLUGIN_CONFIG", {})
    monkeypatch.setenv("SLIPBOX_ATOMIZER_MODEL", "atomiser/model")
    monkeypatch.setenv("SLIPBOX_JUDGE_MODEL", "judge/model")

    seen = {}

    def fake_generate(messages, max_new_tokens=768, max_seconds=None, model_name=None,
                      temperature=None):
        seen["model"] = model_name
        seen["max_seconds"] = max_seconds
        seen["temperature"] = temperature
        return "{}"

    monkeypatch.setattr(models, "judge_generate", fake_generate)
    atomizer._generate_local("instructions", "prompt")
    assert seen["model"] == "atomiser/model"
    assert seen["max_seconds"] == cfg.atomizer_timeout()   # the budget is passed too
    # ...and so is the temperature: it used to reach only the `host` backend, so
    # the documented knob was inert on the default path.
    assert seen["temperature"] == cfg.atomizer_temperature()


def test_generative_models_are_cached_per_name(monkeypatch):
    from slipbox import models

    # One global singleton served whichever role loaded first; keyed by name,
    # the atomiser and the judge can coexist in one process.
    monkeypatch.setattr(models, "_GENERATORS", {})
    monkeypatch.setattr(models, "mount_semantic_venv", lambda: None)
    models._GENERATORS["a/model"] = ("model-a", "tok-a")
    models._GENERATORS["b/model"] = ("model-b", "tok-b")
    assert models.generator("a/model")[0] == "model-a"
    assert models.generator("b/model")[0] == "model-b"
    assert models.judge_resident() is True


def test_reasoning_scratchpad_is_stripped_before_parsing():
    from slipbox import atomizer

    # A hybrid reasoning model prefixes its answer with a <think> block. It is
    # not JSON and nobody parses it, so it must never reach the parser.
    noisy = ("<think>Let me consider the atoms carefully...</think>\n"
             + _plan_json([_atom("Kept")]))
    plan = atomizer.parse_plan(noisy)
    assert [a["title"] for a in plan["atoms"]] == ["Kept"]


def test_unplaced_batches_are_threaded_in_code(repo):
    from slipbox import atomizer

    # The observed small-model failure: every atom comes back new_thread while
    # the prose rationale says "continues...". Orphan top-level threads destroy
    # the one property positional order exists for, so derive the structure.
    plan = atomizer.parse_plan(_plan_json([_atom(f"A{i}") for i in range(4)]))
    assert plan["atoms"][0]["new_thread"] is True          # the head opens it
    assert [a["continues"] for a in plan["atoms"][1:]] == [0, 0, 0]   # a fan...
    assert all(a["new_thread"] is False for a in plan["atoms"][1:])
    # ...and the derivation is disclosed, so a reviewer knows the machine chose
    # this shape rather than the judge.
    assert atomizer.DERIVED_PLACEMENT in plan["atoms"][1]["rationale"]

    # A shallow fan, not a deep chain: 1, 1-a, 1-b, 1-c.
    ops.setup(repo)
    ops.capture("Src", "body text", root=repo)
    entry = ops.inbox(repo)["entries"][0]["path"]
    import json as _json
    original = atomizer._generate
    atomizer._generate = lambda i, p: _plan_json([_atom(f"A{i}") for i in range(4)])
    try:
        result = atomizer.distil(entry, repo)
    finally:
        atomizer._generate = original
    assert [a["proposed_id"] for a in result["atoms"]] == ["1", "1-a", "1-b", "1-c"]


def test_a_model_that_placed_atoms_is_not_second_guessed():
    from slipbox import atomizer

    # Conservative on purpose: any expressed structure means hands off.
    plan = atomizer.parse_plan(_plan_json([
        _atom("head"), _atom("follows", continues=0), _atom("third"),
    ]))
    assert plan["atoms"][2]["continues"] is None       # left exactly as proposed
    assert atomizer.DERIVED_PLACEMENT not in (plan["atoms"][2]["rationale"] or "")

    # A single atom has nothing to thread onto.
    solo = atomizer.parse_plan(_plan_json([_atom("only")]))
    assert solo["atoms"][0]["continues"] is None


def test_skill_bundle_tracks_the_registered_skills(tmp_path, monkeypatch):
    from slipbox import bundle, config as cfg

    monkeypatch.setattr(cfg, "_PLUGIN_CONFIG", {})
    monkeypatch.setenv("HERMES_BUNDLES_DIR", str(tmp_path / "skill-bundles"))

    path = bundle.install(["capture", "adapt", "search"])
    assert path is not None and path.name == "slipbox.yaml"
    text = path.read_text()
    # Entries are QUALIFIED: a bundle resolves members through skill_view(),
    # which reads `plugin:skill` as plugin-provided. Bare names would miss,
    # because plugin skills never enter the flat ~/.hermes/skills tree.
    assert '"slipbox:capture"' in text and '"slipbox:search"' in text
    assert '"slipbox:review"' not in text        # only what was registered

    # Idempotent — hermes caches bundles on mtime, so an identical rewrite
    # every startup would churn the cache for nothing.
    before = path.stat().st_mtime_ns
    bundle.install(["capture", "adapt", "search"])
    assert path.stat().st_mtime_ns == before

    # A changed skill set IS rewritten.
    bundle.install(["search"])
    assert '"slipbox:capture"' not in path.read_text()

    # Switched off, nothing is written.
    monkeypatch.setenv("SLIPBOX_BUNDLE", "0")
    other = tmp_path / "off"
    monkeypatch.setenv("HERMES_BUNDLES_DIR", str(other))
    assert bundle.install(["capture"]) is None
    assert not other.exists()


def test_skill_bundle_is_skipped_when_there_is_no_hermes_home(tmp_path, monkeypatch):
    from slipbox import bundle, config as cfg

    # Guessing ~/.hermes would plant a bundle in whichever profile owns that
    # path. With no hermes and no HERMES_HOME there is nothing to write for.
    monkeypatch.setattr(cfg, "_PLUGIN_CONFIG", {})
    monkeypatch.delenv("HERMES_BUNDLES_DIR", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr(bundle, "_bundles_dir", lambda: None)
    assert bundle.install(["capture"]) is None


def test_persist_schema_does_not_invite_chaining():
    """The tool description is the strongest signal an agent gets.

    A real store was built with a six-level chain (1 → 1-a → 1-a-1 → …) from a
    flat batch of siblings, because this schema still described the pre-proposal
    model: "assign the ID that follows the most-connected note". A diligent agent
    supplied one per atom, and the most-connected note in a batch is the sibling
    it just placed. The skill body said the opposite, and lost — schemas outrank
    prose.
    """
    from slipbox import schemas

    persist = next(s for s in schemas.WRITING if s["name"] == "slipbox_persist")
    after = persist["parameters"]["properties"]["after"]["description"]

    # The default path must be stated as "pass nothing".
    assert "VERBATIM" in persist["description"]
    assert "OVERRIDE ONLY" in after
    # And the specific trap must be named, not merely left unmentioned.
    assert "previously persisted" in after

    # No surface may still describe placement as following the most-connected
    # note — that model died when adapt started assigning the ID.
    for schema in (*schemas.READ_ONLY, *schemas.GATED, *schemas.WRITING):
        blob = str(schema)
        assert "most-connected" not in blob, schema["name"]
        assert "most connected" not in blob, schema["name"]


# --- Regressions found by the end-to-end run ---------------------------------

def test_setup_creates_the_git_repository(tmp_path, monkeypatch):
    """A fresh store must end up under git, or it keeps no audit trail.

    Setup wrote `.gitignore` and called `commit`, which answered `not a git
    repository` and returned quietly, so every capture, atom and placement was
    written with no history and nothing reported it. The `repo` fixture does its
    own `git init`, which is exactly why no test caught this — so this one starts
    from a bare directory.
    """
    from slipbox import gitops

    root = tmp_path / "fresh"
    monkeypatch.setenv("SLIPBOX_REPO", str(root))
    result = ops.setup(root)

    assert (root / ".git").is_dir()
    assert gitops.is_repo(root)
    assert result["git"]["is_repo"] is True
    # An automated commit needs an author; setup provides one where the host has
    # none, so the first commit cannot fail with "tell me who you are".
    assert result["git"]["has_identity"] is True
    assert result["commit"].get("committed") is True
    assert result["git"]["commits"] >= 1

    # Idempotent, and it never re-initialises an existing repository.
    assert ops.setup(root)["git"]["initialized"] is False


def test_setup_leaves_a_store_nested_in_another_repository_alone(tmp_path, monkeypatch):
    """A slipbox kept inside a bigger repo commits to that one, as it always did."""
    from slipbox import gitops

    outer = tmp_path / "outer"
    outer.mkdir()
    subprocess.run(["git", "init", "-q", str(outer)], check=True)
    inner = outer / "kb"
    monkeypatch.setenv("SLIPBOX_REPO", str(inner))

    ops.setup(inner)
    assert not (inner / ".git").exists()
    assert gitops.is_repo(inner)   # via the outer work tree


def test_a_function_word_does_not_match_every_topic(repo):
    """Topic overlap counts content words only.

    "and" shared with a topic title used to be enough to nominate every note
    under it, so an unrelated query swept the whole store into the positional
    layer — and those notes then outranked the real answer.
    """
    indexmd.add(repo, ["Storage engines", "Write-ahead logging and durability"], "1", "A")
    indexmd.add(repo, ["Craft", "Paper marbling"], "2", "B")

    assert indexmd.entry_ids(repo, "recipes for sourdough and pizza") == []
    assert indexmd.entry_ids(repo, "carrageenan size and ox gall") == []
    # A genuine content-word overlap still matches, and only the right topic.
    assert indexmd.entry_ids(repo, "how is durability bounded?") == ["1"]
    assert indexmd.entry_ids(repo, "marbling paper by hand") == ["2"]


def test_both_layers_is_a_prior_not_a_filter():
    """A near vector neighbour outranks weak candidates both layers happened to hit.

    `both_layers` was the primary sort key, so thirteen loosely-related notes
    that a spurious topic match had swept in pushed the single nearest neighbour
    off the end of the truncated candidate list.
    """
    from slipbox import lookup

    near = {"path": "source/marbling.md", "distance": 0.44, "both_layers": False}
    corroborated = [{"path": f"store/{i}.md", "distance": 0.69, "both_layers": True}
                    for i in range(13)]
    ranked = sorted([*corroborated, near], key=lookup._rank)
    assert ranked[0] is near

    # It still breaks a tie between near-equals — that is what a prior is for.
    a = {"path": "store/a.md", "distance": 0.50, "both_layers": False}
    b = {"path": "store/b.md", "distance": 0.51, "both_layers": True}
    assert sorted([a, b], key=lookup._rank)[0] is b

    # A positional-only candidate has no distance and takes the neutral one:
    # below what the embedder liked, above what it rejected.
    positional_only = {"path": "store/p.md", "both_layers": False}
    assert lookup._rank(near) < lookup._rank(positional_only) < lookup._rank(corroborated[0])


def test_readonly_refuses_a_write_handler(repo, monkeypatch):
    """The mode belongs on the operation, not only on the registered menu.

    Read-only was enforced at registration and in the CLI, but the handlers
    themselves still ran, so anything holding a reference to one wrote and
    committed to a store the operator had marked read-only.
    """
    import json

    from slipbox import schemas, tools

    monkeypatch.setenv("SLIPBOX_READONLY", "1")
    result = json.loads(tools.HANDLERS["slipbox_capture"](
        {"title": "x", "content": "y"}))
    assert result.get("readonly") is True
    assert "read-only" in result["error"]
    assert not list((repo / config.INBOX).glob("*.md"))

    # Every write tool, including the branch of setup that loops over repos.
    for schema in schemas.WRITING:
        refusal = json.loads(tools.HANDLERS[schema["name"]]({}))
        assert refusal.get("readonly") is True, schema["name"]

    # Reads are untouched.
    assert json.loads(tools.HANDLERS["slipbox_status"]({})).get("readonly") is None


def test_any_load_failure_degrades_rather_than_crashing_a_write(repo, monkeypatch):
    """Degradation must not depend on the failure being exactly ImportError.

    `embed` catches `ModelUnavailable` and `_embed_vector` catches the
    `EmbeddingError` it becomes. Anything else — a broken weights cache, a driver
    error, a version clash raising ValueError inside transformers — escaped both
    and aborted the capture instead of deferring the vector.
    """
    from slipbox import embeddings, models

    def explode():
        raise ValueError("pytest.__spec__ is None")

    monkeypatch.setattr(models, "_EMBEDDER", None)
    monkeypatch.setattr(models, "mount_semantic_venv", explode)

    with pytest.raises(models.ModelUnavailable):
        models.embedder()
    with pytest.raises(embeddings.EmbeddingError):
        embeddings.embed_doc("text")

    # And the write still lands, with the vector deferred rather than lost.
    result = ops.create_atom("A claim", "A body.", candidates=["new-thread"], root=repo)
    assert result["embedding"].startswith("deferred")
    assert (repo / result["path"]).is_file()


def test_the_plugin_source_tree_is_never_the_default_store(tmp_path, monkeypatch):
    """An unconfigured instance must not adopt the plugin's own checkout.

    `root()` falls back to the parent of the package — the `<repo>/slipbox`
    deployment, where the plugin is installed *into* the knowledge base. But
    `Path(__file__).resolve()` follows symlinks, and the documented dev install
    is one (`ln -sfn "$PWD/slipbox" ~/.hermes/plugins/slipbox`), so the parent
    resolved back to the git checkout: an unconfigured instance created `inbox/`,
    `store/` and `embeddings.db` in the source tree and committed notes into the
    project's history, reporting success throughout.
    """
    monkeypatch.delenv("SLIPBOX_REPO", raising=False)
    monkeypatch.delenv("SLIPBOX_ROOT", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    project = Path(config.__file__).resolve().parent.parent
    assert config._is_source_checkout(project)      # this very repository
    assert config.root() != project
    assert config.root() == (tmp_path / "data" / "slipbox").resolve()
    assert "source checkout" in config.root_origin()

    # A real `<repo>/slipbox` deployment — the package without the test suite —
    # still takes the knowledge base beside it.
    deployment = tmp_path / "kb"
    (deployment / "slipbox").mkdir(parents=True)
    (deployment / "slipbox" / "plugin.yaml").write_text("name: slipbox\n")
    assert not config._is_source_checkout(deployment)

    # And an explicit setting always wins over either rule.
    monkeypatch.setenv("SLIPBOX_REPO", str(deployment))
    assert config.root() == deployment.resolve()
    assert config.root_origin() == "SLIPBOX_REPO"
