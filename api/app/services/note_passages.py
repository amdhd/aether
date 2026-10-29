"""Bounded, source-addressable evidence from retrieved notes."""
import re
from bisect import bisect_left
from collections import defaultdict
from typing import Any

from app.models.note import Note

PASSAGE_CHARS = 900
MAX_PASSAGES = 2
READ_CHARS = 2000
_STOPWORDS = frozenset("the and for with from what where when which how does this that have about your my".split())


def search_evidence(note: Note, query: str) -> dict[str, Any]:
    text = note.content
    terms = {word.casefold() for word in re.findall(r"\w+", query) if len(word) > 2}
    terms -= _STOPWORDS
    # Match in the original text so Unicode case folding never changes offsets.
    matches = [(m.start(), m.group().casefold()) for m in re.finditer(r"\w+", text)
               if m.group().casefold() in terms]
    starts = {0}
    starts.update(max(0, min(position - PASSAGE_CHARS // 3, len(text) - PASSAGE_CHARS))
                  for position, _ in matches)

    positions: dict[str, list[int]] = defaultdict(list)
    for position, term in matches:
        positions[term].append(position)

    def score(start: int) -> tuple[int, int]:
        # Binary searches avoid rescanning every match for every candidate on
        # long repetitive notes. At most one lookup per distinct query term.
        covered = 0
        for offsets in positions.values():
            index = bisect_left(offsets, start)
            covered += index < len(offsets) and offsets[index] < start + PASSAGE_CHARS
        return covered, -start

    selected: list[int] = []
    for start in sorted(starts, key=score, reverse=True):
        if all(abs(start - previous) >= PASSAGE_CHARS for previous in selected):
            selected.append(start)
        if len(selected) == MAX_PASSAGES:
            break
    passages = [{"start": start, "end": min(start + PASSAGE_CHARS, len(text)),
                 "text": text[start:start + PASSAGE_CHARS]} for start in sorted(selected)]
    return {
        "id": note.id, "title": note.title,
        "passages": passages, "content_length": len(text),
        "content_truncated": sum(len(p["text"]) for p in passages) < len(text),
        "read_more": "Use read_note with note_id and offset to inspect omitted text. Offsets are character positions.",
    }
