"""`index.md` — the nested topic map pointing at entry notes.

The whitepaper's structural layer (§"Retrieval Architecture"): a hierarchical set
of *bookmarks*, each saying "the next ~N notes are about X" until the next entry.
The interval a topic covers is **derived at query time** as `[entry, next-entry)`
— the right bound is never stored and can never go stale. The file is a plain
nested markdown list; every item may carry wikilinks to the notes that open the
topic:

```markdown
# Index

- Attention
  - Deep work — [[21]] Distraction has a switching cost
- Note-taking systems — [[7]] Folgezettel beats tags
```

Step 1 of the shared lookup mechanism parses it into a topic → entry-note map.
`index.md` is a rebuildable derivative, never the source of truth.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import config, notes
# Imported as a name rather than a module: `text` is already a local variable in
# this file (an index line's content), and the shadowing would be a trap.
from .text import tokens as _tokens

ITEM_RE = re.compile(r"^(?P<indent>[ \t]*)[-*+][ \t]+(?P<text>.*\S)[ \t]*$")
HEADING = "# Index"


def path_of(root: Path) -> Path:
    return root / config.INDEX_FILE


def _lines(root: Path) -> list[str]:
    path = path_of(root)
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _indent_unit(raw_indents: list[int]) -> int:
    positive = [i for i in raw_indents if i > 0]
    return min(positive) if positive else 2


def parse(root: Path) -> list[dict]:
    """Flat list of topics, each with its ancestor path and entry-note IDs."""
    lines = _lines(root)
    matches = [(i, ITEM_RE.match(line)) for i, line in enumerate(lines)]
    items = [(i, m) for i, m in matches if m]
    unit = _indent_unit([len(m.group("indent").expandtabs(2)) for _, m in items])

    entries: list[dict] = []
    stack: list[tuple[int, str]] = []  # (level, topic)
    for line_no, match in items:
        level = len(match.group("indent").expandtabs(2)) // unit
        text = match.group("text")
        ids = [t.strip() for t in notes.wikilinks(text)]
        # The topic is what precedes the first wikilink; everything from there on
        # is the list of entry notes ("Deep work — [[21]] Switching costs").
        first_link = notes.WIKILINK_RE.search(text)
        topic = (text[: first_link.start()] if first_link else text)
        topic = topic.strip().rstrip(" \t-—–:·|").strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        ancestors = [t for _, t in stack]
        stack.append((level, topic))
        entries.append({
            "topic": topic,
            "path": [*ancestors, topic],
            "ids": ids,
            "level": level,
            "line": line_no + 1,
        })
    return entries


def tree(root: Path) -> list[dict]:
    """The same data nested, for `slipbox_index`."""
    roots: list[dict] = []
    stack: list[tuple[int, dict]] = []
    for entry in parse(root):
        node = {"topic": entry["topic"], "ids": entry["ids"], "children": []}
        while stack and stack[-1][0] >= entry["level"]:
            stack.pop()
        (stack[-1][1]["children"] if stack else roots).append(node)
        stack.append((entry["level"], node))
    return roots


def match(root: Path, query: str, limit: int = 5) -> list[dict]:
    """Topics whose wording overlaps the query, best first (lookup step 1).

    Overlap is counted over *content* words only (`text.tokens`). Counting
    function words made a single "and" shared with a topic title enough to
    nominate every note under it, which is how an unrelated query used to sweep
    the whole store into the positional layer.
    """
    wanted = _tokens(query)
    if not wanted:
        return []
    scored = []
    for entry in parse(root):
        if not entry["ids"]:
            continue
        haystack = _tokens(" ".join(entry["path"]))
        overlap = len(wanted & haystack)
        if overlap:
            scored.append((overlap / len(wanted), entry))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["line"]))
    return [{**entry, "score": round(score, 3)} for score, entry in scored[:limit]]


def entry_ids(root: Path, query: str, limit: int = 5) -> list[str]:
    found: list[str] = []
    for entry in match(root, query, limit):
        found.extend(entry["ids"])
    return list(dict.fromkeys(found))


# --- Writing -----------------------------------------------------------------

def _normalise(topic: str) -> str:
    return re.sub(r"\s+", " ", (topic or "").strip().lower())


def add(root: Path, topic_path, note_id: str, title: str = "") -> dict:
    """Place `[[note_id]]` under a (possibly new) nested topic path.

    Missing levels are created; an existing topic gains the link unless it is
    already there. Returns what changed, so `slipbox:link` can report it.
    """
    topic_path = [str(t).strip() for t in (topic_path or []) if str(t).strip()]
    if not topic_path:
        raise ValueError("topic path is empty")

    path = path_of(root)
    lines = _lines(root)
    if not lines:
        lines = [HEADING, ""]

    items = [(i, ITEM_RE.match(line)) for i, line in enumerate(lines)]
    items = [(i, m) for i, m in items if m]
    unit = _indent_unit([len(m.group("indent").expandtabs(2)) for _, m in items])

    parsed = parse(root)
    label = f"[[{note_id}]]" + (f" {title}".rstrip() if title else "")

    def find(prefix: list[str]) -> dict | None:
        wanted = [_normalise(t) for t in prefix]
        return next(
            (e for e in parsed if [_normalise(t) for t in e["path"]] == wanted), None
        )

    matched: dict | None = None
    for d in range(len(topic_path)):
        found = find(topic_path[: d + 1])
        if found is None:
            break
        matched = found

    if matched and len(matched["path"]) == len(topic_path):
        line_no = matched["line"] - 1
        if f"[[{note_id}]]" in lines[line_no]:
            return {"index": config.INDEX_FILE, "topic": topic_path, "added": False,
                    "reason": "already listed"}
        separator = " · " if notes.wikilinks(lines[line_no]) else " — "
        lines[line_no] = lines[line_no].rstrip() + separator + label
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"index": config.INDEX_FILE, "topic": topic_path, "added": True,
                "created_levels": 0}

    start_level = len(matched["path"]) if matched else 0
    insert_at = _end_of_subtree(lines, matched, unit) if matched else _end_of_list(lines)
    missing = topic_path[start_level:]
    block = []
    for offset, topic in enumerate(missing):
        indent = " " * (unit * (start_level + offset))
        suffix = f" — {label}" if offset == len(missing) - 1 else ""
        block.append(f"{indent}- {topic}{suffix}")
    lines[insert_at:insert_at] = block
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"index": config.INDEX_FILE, "topic": topic_path, "added": True,
            "created_levels": len(missing)}


def _end_of_subtree(lines: list[str], entry: dict, unit: int) -> int:
    own_level = entry["level"]
    cursor = entry["line"]  # 1-based line of the entry == index of the next line
    last = cursor
    for index in range(cursor, len(lines)):
        match = ITEM_RE.match(lines[index])
        if not match:
            if lines[index].strip():
                break
            continue
        level = len(match.group("indent").expandtabs(2)) // unit
        if level <= own_level:
            break
        last = index + 1
    return last


def _end_of_list(lines: list[str]) -> int:
    for index in range(len(lines) - 1, -1, -1):
        if ITEM_RE.match(lines[index]):
            return index + 1
    return len(lines)


def remove(root: Path, note_id: str) -> bool:
    """Drop every `[[note_id]]` reference (used when a rejected note is purged)."""
    lines = _lines(root)
    changed = False
    output = []
    for line in lines:
        if f"[[{note_id}]]" not in line:
            output.append(line)
            continue
        changed = True
        stripped = re.sub(r"\s*[—·]?\s*\[\[" + re.escape(note_id) + r"\]\][^\n·—]*", "", line)
        if ITEM_RE.match(stripped) and stripped.strip(" -*+\t"):
            output.append(stripped.rstrip())
    if changed:
        path_of(root).write_text("\n".join(output) + "\n", encoding="utf-8")
    return changed
