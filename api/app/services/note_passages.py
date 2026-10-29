"""Bounded, source-addressable evidence from retrieved notes."""
import json
import re
from bisect import bisect_left
from collections import defaultdict
from typing import Any

from app.agent.budget import MAX_TOOL_RESULT_BYTES
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


# Leave room for the untrusted-tool wrapper and prompt framing under the
# per-message context ceiling. Drop whole records rather than malformed JSON.
SEARCH_EVIDENCE_BYTES = MAX_TOOL_RESULT_BYTES - 2000
SEARCH_QUERY_CHARS = 200
EVIDENCE_VERSION = "passages-v1"


def build_search_payload(notes: list[Note], query: str) -> dict[str, Any]:
    query = query[:SEARCH_QUERY_CHARS]
    result: dict[str, Any] = {"notes": [search_evidence(note, query) for note in notes]}
    while len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > SEARCH_EVIDENCE_BYTES:
        if not result["notes"]:
            return {"notes": [], "truncated": True}
        result["notes"].pop()
        result["truncated"] = True
    return result


def evidence_contexts(payload: dict[str, Any]) -> list[str]:
    """One context per delivered note, preserving source IDs and offsets.

    Eval judges receive only records that survived the same payload budget as
    production. Neither answer generation nor recall may see discarded notes.
    """
    return [json.dumps(record, ensure_ascii=False) for record in payload["notes"]]
