"""The note model: frontmatter, wikilinks and the repository layout.

Only the standard library is used, so the frontmatter parser understands the
small YAML subset the plugin itself writes: `key: scalar`, inline lists
(`[a, b]`) and block lists (`- item`). Nested structures — the `stage/` review
object, the `placement` cache and the `duplicate` signal — are written and read
as compact JSON on a single line (`key: {"a": "b"}`), which is valid inline YAML
and needs no third-party parser.

The `id` frontmatter field is *self-describing* about a note's kind (project
directive): a store atom's `id` is a scalar Folgezettel position, a stage atom's
`id` is the **list** of suggested placement candidates (not yet placed), and a
source note's `id` is a **UUID** (identity only — sources are footnotes with no
position). See the whitepaper §"Repository and formats".
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from datetime import date
from pathlib import Path

from . import config, folgezettel

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---[ \t]*\n?", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|([^\]]*))?\]\]")
_UNSAFE_YAML = re.compile(r"^[\s>|&*!%@`{\[\]}#,]|[:#]\s|[\"']|\s$")

# Typed connection verbs the whitepaper reserves in note bodies — recognised so
# the tools can report them, though the wikilink itself is the source of truth.
LINK_VERBS = ("extends", "refines", "contradicts", "supersedes", "variant_of",
              "corrects", "source")

# The subset the atomiser may *propose* as an atom's connections. `source` is the
# citation and is written from the source note, not chosen; `variant_of` is a
# reviewer's verdict with its own frontmatter slot. What remains is the semantic
# graph — how one idea stands to another — which is the whole point of linking
# and, until the plan schema gained a slot for it, was never emitted at all.
CONNECTION_VERBS = ("extends", "refines", "contradicts", "supersedes", "corrects")


# --- Frontmatter -------------------------------------------------------------

def _scalar(raw: str):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    # Nested structures the plugin writes as compact JSON (review, placement…).
    if raw.startswith("{") or (raw.startswith("[") and "{" in raw):
        try:
            return json.loads(raw)
        except ValueError:
            pass
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_scalar(part) for part in inner.split(",")]
    return raw


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a note into (frontmatter mapping, body)."""
    match = FRONTMATTER_RE.match(text or "")
    if not match:
        return {}, text or ""
    data: dict = {}
    key: str | None = None
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("- ") and key is not None:
            item = _scalar(line.lstrip()[2:])
            existing = data.get(key)
            if isinstance(existing, list):
                existing.append(item)
            else:
                data[key] = [item] if existing in ("", None) else [existing, item]
            continue
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        data[key] = _scalar(raw)
    return data, text[match.end():]


def _dump_scalar(value) -> str:
    text = "" if value is None else str(value)
    if text == "" or _UNSAFE_YAML.search(text):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def _is_nested(value) -> bool:
    if isinstance(value, dict):
        return True
    return isinstance(value, (list, tuple)) and any(isinstance(v, (dict, list)) for v in value)


def dump_frontmatter(data: dict) -> str:
    """Serialise a mapping into a YAML frontmatter block (keys keep order)."""
    lines = ["---"]
    for key, value in data.items():
        if value is None or value == []:
            continue
        if _is_nested(value):
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
        elif isinstance(value, (list, tuple)):
            lines.append(f"{key}:")
            lines.extend(f"  - {_dump_scalar(item)}" for item in value)
        else:
            lines.append(f"{key}: {_dump_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def compose(frontmatter: dict, body: str) -> str:
    body = (body or "").strip()
    return dump_frontmatter(frontmatter) + ("\n" + body + "\n" if body else "")


# --- Helpers -----------------------------------------------------------------

def slugify(text: str, limit: int = 60) -> str:
    text = (text or "").replace("ł", "l").replace("Ł", "L")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:limit].strip("-") or "note"


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def new_uuid() -> str:
    """A source note's identity — a UUID, not a position (sources are footnotes)."""
    return str(uuid.uuid4())


def today() -> str:
    return date.today().isoformat()


def wikilinks(text: str) -> list[str]:
    """Targets of every `[[target]]` / `[[target|label]]` in the text."""
    return [m.group(1).strip() for m in WIKILINK_RE.finditer(text or "")]


def typed_links(text: str) -> list[dict]:
    """`(verb, target)` pairs like `contradicts [[21-a]]` in a note body."""
    out: list[dict] = []
    for verb in LINK_VERBS:
        for match in re.finditer(
            rf"\b{verb}\b[:\s]+\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]", text or "", re.IGNORECASE
        ):
            out.append({"verb": verb.lower(), "target": match.group(1).strip()})
    return out


def as_list(value) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


# --- Note --------------------------------------------------------------------

class Note:
    """A markdown note on disk, parsed lazily-enough for our purposes."""

    def __init__(self, path: Path, root: Path):
        self.path = path
        self.root = root
        self.rel = path.relative_to(root).as_posix()
        self.stem = path.stem
        self.text = path.read_text(encoding="utf-8", errors="replace")
        self.frontmatter, self.body = parse_frontmatter(self.text)

    # -- frontmatter accessors
    def get(self, key: str, default=None):
        value = self.frontmatter.get(key, default)
        return default if value in ("", None) else value

    @property
    def id(self) -> str:
        """Scalar id — the Folgezettel position for a store atom, the UUID for a
        source. A stage atom's id is a *list* of candidates, so this returns ""."""
        value = self.frontmatter.get("id")
        return "" if isinstance(value, (list, tuple)) else str(value or "")

    @property
    def title(self) -> str:
        return str(self.get("title", "") or self.stem)

    @property
    def sources(self) -> list[str]:
        return as_list(self.frontmatter.get("source"))

    @property
    def links(self) -> list[str]:
        return wikilinks(self.text)

    @property
    def space(self) -> str | None:
        """Which vector space this note belongs to, if any."""
        if self.rel.startswith(config.STAGE + "/"):
            return config.SPACE_STAGE
        if self.rel.startswith(config.STORE + "/"):
            return config.SPACE_STORE
        if self.rel.startswith(config.SOURCE + "/"):
            return config.SPACE_SOURCE
        if self.rel.startswith(config.SYNTHESIS + "/"):
            return config.SPACE_SYNTHESIS
        return None

    @property
    def cites(self) -> list[str]:
        """Note IDs a synthesis rests on — its `cites` frontmatter, as bare IDs.

        The citation list is the load-bearing part of a synthesis: it is what
        makes the document checkable (every claim resolves to an atom), what
        `drift` measures against, and what `coverage` counts. Kept in frontmatter
        rather than scraped from the prose so it is a *declared* dependency, not
        an inferred one — a synthesis that cites nothing is a synthesis that
        proves nothing, and that should be visible without parsing English.
        """
        return [str(c).strip().strip("[]").split("/")[-1]
                for c in as_list(self.frontmatter.get("cites")) if str(c).strip()]

    @property
    def key(self) -> str:
        """Stable key inside a vector space: the ID for store notes, else the stem."""
        return self.id if self.space == config.SPACE_STORE and self.id else self.stem

    def embed_text(self) -> str:
        """What gets embedded: the title plus the body, without frontmatter.

        A synthesis leads with the *question* it answers. Its channel exists to
        recognise "this road has been walked before", so a query is matched
        against a question, not against prose — and the prose is long, so leaving
        the question out diluted the very signal the channel is for. Measured on
        a real store: the same question-to-synthesis pair sat at cosine 0.43
        embedded as title+body, comfortably past the lead threshold, and the
        channel never fired once.
        """
        question = str(self.get("question", "") or "").strip()
        if self.space == config.SPACE_SYNTHESIS and question:
            return f"{question}\n\n{self.title}\n\n{self.body.strip()}".strip()
        return f"{self.title}\n\n{self.body.strip()}".strip()

    def hash(self) -> str:
        return content_hash(self.embed_text())

    def summary(self) -> dict:
        data = {"key": self.key, "title": self.title, "path": self.rel}
        if self.id:
            data["id"] = self.id
        return data

    def write(self, frontmatter: dict | None = None, body: str | None = None) -> None:
        self.frontmatter = frontmatter if frontmatter is not None else self.frontmatter
        self.body = body if body is not None else self.body
        self.text = compose(self.frontmatter, self.body)
        self.path.write_text(self.text, encoding="utf-8")


# --- Layout ------------------------------------------------------------------

def ensure_layout(root: Path) -> None:
    for name in config.DIRECTORIES:
        (root / name).mkdir(parents=True, exist_ok=True)


def _markdown_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.md") if not p.name.startswith("."))


def load(root: Path, relative: str) -> Note | None:
    path = root / relative
    return Note(path, root) if path.is_file() else None


def inbox_notes(root: Path) -> list[Note]:
    return [Note(p, root) for p in _markdown_files(root / config.INBOX)]


def source_notes(root: Path) -> list[Note]:
    return [Note(p, root) for p in _markdown_files(root / config.SOURCE)]


def stage_notes(root: Path) -> list[Note]:
    return [Note(p, root) for p in _markdown_files(root / config.STAGE)]


def synthesis_notes(root: Path) -> list[Note]:
    """Newest first — a synthesis is read as "the latest view", not as a sequence."""
    notes = [Note(p, root) for p in _markdown_files(root / config.SYNTHESIS)]
    return sorted(notes, key=lambda n: str(n.get("created") or ""), reverse=True)


def store_notes(root: Path) -> list[Note]:
    """Store notes in Folgezettel order."""
    found = [Note(p, root) for p in _markdown_files(root / config.STORE)]
    ranked = {n.stem: folgezettel.sort_key(n.stem) for n in found if folgezettel.is_valid(n.stem)}
    return sorted(
        (n for n in found if n.stem in ranked), key=lambda n: ranked[n.stem]
    ) + [n for n in found if n.stem not in ranked]


def store_ids(root: Path) -> list[str]:
    return folgezettel.order(p.stem for p in _markdown_files(root / config.STORE))


def notes_in_space(root: Path, space: str) -> list[Note]:
    return {
        config.SPACE_STORE: store_notes,
        config.SPACE_SOURCE: source_notes,
        config.SPACE_STAGE: stage_notes,
        config.SPACE_SYNTHESIS: synthesis_notes,
    }[space](root)


def resolve(root: Path, ident: str) -> Note | None:
    """Find a note by Folgezettel ID, path, filename stem or title.

    Order matters: an ID always wins, so `slipbox_show 21-a` cannot be hijacked
    by a source note that happens to be titled "21-a".
    """
    ident = (ident or "").strip().strip("[]")
    if not ident:
        return None

    if folgezettel.is_valid(ident):
        note = load(root, f"{config.STORE}/{ident}.md")
        if note:
            return note

    candidate = (root / ident).with_suffix(".md") if not ident.endswith(".md") else root / ident
    try:
        candidate.relative_to(root)
    except ValueError:
        return None  # never escape the repository
    if candidate.is_file():
        return Note(candidate, root)

    pool = store_notes(root) + source_notes(root) + stage_notes(root) + inbox_notes(root)
    for note in pool:
        if ident in (note.stem, note.rel, note.id):
            return note
    lowered = ident.lower()
    for note in pool:
        if note.title.lower() == lowered:
            return note
    return None


def resolver(pool: list[Note]):
    """Build a one-pass link resolver over `pool` (earlier notes win)."""
    table: dict[str, Note] = {}
    for note in pool:
        keys = [note.id, note.stem, note.rel]
        if note.rel.endswith(".md"):
            keys.append(note.rel[:-3])
        for key in keys:
            if key:
                table.setdefault(key, note)
        table.setdefault(note.title.lower(), note)

    def resolve_link(target: str) -> Note | None:
        cleaned = (target or "").strip().strip("[]")
        return table.get(cleaned) or table.get(cleaned.lower())

    return resolve_link


def unique_path(directory: Path, stem: str, suffix: str = ".md") -> Path:
    """`stem.md`, or `stem-2.md`, `stem-3.md`… when taken."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}{suffix}"
    counter = 2
    while path.exists():
        path = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return path


_EMBED_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")


def attachments_of(note: Note) -> list[str]:
    """Attachment filenames declared in frontmatter or embedded in the body."""
    declared = as_list(note.frontmatter.get("attachments"))
    embedded = [m.group(1) for m in _EMBED_RE.finditer(note.text)]
    embedded += [t for t in wikilinks(note.text) if ".attachments/" in t]
    names = [Path(x).name for x in declared + embedded if ".attachments/" in x or x in declared]
    return list(dict.fromkeys(names))
