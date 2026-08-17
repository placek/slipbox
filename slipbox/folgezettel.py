"""Folgezettel identifiers — parsing, ordering and allocation.

An ID encodes a position in a sequence of thought, following Luhmann's principle
(whitepaper §"Ordering"): alternating digit and letter segments joined with
hyphens (`21`, `21-a`, `21-a-1`, `21-a-1-b`).

Ordering: split on `-`, digit segments compared numerically, letter segments
alphabetically. A prefix sorts before its extensions, so `21` < `21-a` < `21-b`
< `22` and — unlike lexicographic sorting — `21-a-2` < `21-a-10`. This single
invariant is what makes positional retrieval possible: *identifier adjacency
approximates semantic adjacency*.

Letter segments beyond `z` continue bijectively (`aa`, `ab`, …); to keep those in
sequence order they are compared by (length, text): `z` < `aa`.
"""
from __future__ import annotations

import re

ID_RE = re.compile(r"^\d+(?:-[a-z]+-\d+)*(?:-[a-z]+)?$")
_LETTERS = "abcdefghijklmnopqrstuvwxyz"


class InvalidId(ValueError):
    """The string is not a well-formed Folgezettel ID."""


def is_valid(note_id: str) -> bool:
    return bool(ID_RE.match((note_id or "").strip()))


def segments(note_id: str) -> list[str]:
    """Raw segments of an ID, validated."""
    note_id = (note_id or "").strip()
    if not ID_RE.match(note_id):
        raise InvalidId(f"not a Folgezettel ID: {note_id!r}")
    return note_id.split("-")


def depth(note_id: str) -> int:
    return len(segments(note_id))


def sort_key(note_id: str) -> tuple:
    """Key giving the linear Folgezettel order (see module docstring)."""
    out: list = []
    for i, seg in enumerate(segments(note_id)):
        out.append(int(seg) if i % 2 == 0 else (len(seg), seg))
    return tuple(out)


def order(ids) -> list[str]:
    """Sort IDs into Folgezettel order, dropping malformed ones."""
    return sorted((i for i in ids if is_valid(i)), key=sort_key)


def parent(note_id: str) -> str | None:
    segs = segments(note_id)
    return "-".join(segs[:-1]) if len(segs) > 1 else None


def ancestors(note_id: str) -> list[str]:
    """All ancestors, outermost first: `21-a-1` → [`21`, `21-a`]."""
    segs = segments(note_id)
    return ["-".join(segs[: i + 1]) for i in range(len(segs) - 1)]


def is_ancestor(candidate: str, note_id: str) -> bool:
    return candidate != note_id and note_id.startswith(candidate + "-")


def children(note_id: str, known) -> list[str]:
    """Direct children of `note_id` among `known`, in order."""
    want = depth(note_id) + 1
    return order(i for i in known if is_ancestor(note_id, i) and depth(i) == want)


def descendants(note_id: str, known) -> list[str]:
    return order(i for i in known if is_ancestor(note_id, i))


def siblings(note_id: str, known) -> list[str]:
    """Notes sharing the same parent (the note itself excluded)."""
    par = parent(note_id)
    d = depth(note_id)
    if par is None:
        return order(i for i in known if depth(i) == 1 and i != note_id)
    return order(
        i for i in known if i != note_id and depth(i) == d and is_ancestor(par, i)
    )


def branch_root(note_id: str) -> str:
    """Top-level thread an ID belongs to: `21-a-1` → `21`."""
    return segments(note_id)[0]


# --- Segment arithmetic ------------------------------------------------------

def next_letter(letter: str | None) -> str:
    """`None` → `a`, `a` → `b`, `z` → `aa`, `az` → `ba` (bijective base-26)."""
    if not letter:
        return "a"
    chars = list(letter)
    i = len(chars) - 1
    while i >= 0:
        if chars[i] != "z":
            chars[i] = _LETTERS[_LETTERS.index(chars[i]) + 1]
            return "".join(chars)
        chars[i] = "a"
        i -= 1
    return "a" + "".join(chars)


def next_number(number: str | None) -> str:
    return "1" if not number else str(int(number) + 1)


def _next_segment(previous: str | None, index: int) -> str:
    """Next segment value at position `index` (0-based: even = digits)."""
    return next_number(previous) if index % 2 == 0 else next_letter(previous)


# --- Allocation --------------------------------------------------------------

def new_thread(known) -> str:
    """The next free top-level number — a new thread."""
    tops = [int(i) for i in known if is_valid(i) and depth(i) == 1]
    return str(max(tops) + 1) if tops else "1"


def allocate_after(target: str | None, known) -> str:
    """Assign an ID for a note placed *after* `target`.

    Continuing a thread descends one level: the first note after `21` is `21-a`,
    the next one `21-b`; a note placed after `21-a` becomes `21-a-1` — which is
    exactly the "insert between `21-a` and `21-b`" rule, since every descendant
    of `21-a` precedes `21-b` in the linear order. `target=None` opens a new
    thread. Existing IDs are never reused or renumbered.
    """
    known = set(known)
    if target is None:
        return new_thread(known)
    target = target.strip()
    if not is_valid(target):
        raise InvalidId(f"not a Folgezettel ID: {target!r}")

    index = depth(target)  # 0-based position of the segment being added
    taken = children(target, known)
    previous = segments(taken[-1])[-1] if taken else None
    while True:
        candidate = f"{target}-{_next_segment(previous, index)}"
        if candidate not in known:
            return candidate
        previous = segments(candidate)[-1]


# --- Navigation --------------------------------------------------------------

def neighbourhood(note_id: str, known, n: int) -> list[str]:
    """A window of ±n IDs around `note_id` in Folgezettel order.

    Works for IDs that are not (yet) part of `known` — the window is taken around
    the position the ID *would* occupy.
    """
    ordered = order(known)
    key = sort_key(note_id)
    pos = 0
    for pos, other in enumerate(ordered):  # noqa: B007 - pos used after loop
        if sort_key(other) >= key:
            break
    else:
        pos = len(ordered)
    return ordered[max(0, pos - n): pos + n + 1]


def tree(note_id: str, known) -> dict:
    """Ancestors, siblings and descendants — "where am I in the thread"."""
    known = set(known)
    return {
        "id": note_id,
        "thread": branch_root(note_id),
        "ancestors": [i for i in ancestors(note_id) if i in known],
        "siblings": siblings(note_id, known),
        "children": children(note_id, known),
        "descendants": descendants(note_id, known),
    }
