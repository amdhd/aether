import json
from types import SimpleNamespace

from app.agent.tools import call_tool
from app.models.note import Note
from app.models.user import User
from app.services.note_passages import MAX_PASSAGES, PASSAGE_CHARS, READ_CHARS, search_evidence
from tests.conftest import TestingSessionLocal


def test_search_passages_include_evidence_beyond_old_prefix():
    content = "Routine background. " * 300 + "The launch budget is RM 42000. " + "Other details. " * 100
    note = Note(id=7, title="Plan", content=content)
    evidence = search_evidence(note, "What is the launch budget?")
    assert any("RM 42000" in passage["text"] for passage in evidence["passages"])
    assert sum(len(p["text"]) for p in evidence["passages"]) <= PASSAGE_CHARS * MAX_PASSAGES
    for passage in evidence["passages"]:
        assert content[passage["start"]:passage["end"]] == passage["text"]
    assert evidence["content_truncated"]


def test_unicode_offsets_and_no_keyword_match_still_expose_read_path():
    note = Note(id=1, title="Plan", content="İ🌏" * 2000 + "预算是五万元")
    evidence = search_evidence(note, "预算是五万元")
    for passage in evidence["passages"]:
        assert note.content[passage["start"]:passage["end"]] == passage["text"]
    fallback = search_evidence(note, "semantic paraphrase")
    assert fallback["id"] == 1
    assert fallback["content_truncated"]
    assert "read_note" in fallback["read_more"]


async def test_search_tool_and_read_pages_preserve_tail_and_ownership(monkeypatch):
    from app.services import embeddings
    monkeypatch.setattr(embeddings, "embeddings_enabled", lambda: False)
    async with TestingSessionLocal() as db:
        owner = User(email="passage@example.com", name="Owner", password_hash="unused")
        other = User(email="other-passage@example.com", name="Other", password_hash="unused")
        db.add_all([owner, other])
        await db.flush()
        body = "Background. " * 400 + "The launch budget is RM 42000."
        note = Note(user_id=owner.id, title="Plan", content=body)
        private = Note(user_id=other.id, title="Plan", content="launch budget PRIVATE")
        db.add_all([note, private])
        await db.commit()
        result = json.loads(await call_tool("search_notes", {"query": "launch budget"}, db, owner))
        assert [n["id"] for n in result["notes"]] == [note.id]
        assert "RM 42000" in json.dumps(result)
        offset, recovered = 0, ""
        while offset is not None:
            page = json.loads(await call_tool("read_note", {"note_id": note.id, "offset": offset}, db, owner))["note"]
            assert len(page["content"]) <= READ_CHARS
            recovered += page["content"]
            offset = page["next_offset"]
        assert recovered == body
        denied = json.loads(await call_tool("read_note", {"note_id": private.id}, db, owner))
        missing = json.loads(await call_tool("read_note", {"note_id": 999999}, db, owner))
        assert denied == missing == {"error": "Note not found."}
        invalid = json.loads(await call_tool("read_note", {"note_id": note.id, "offset": -1}, db, owner))
        assert "error" in invalid


async def test_read_note_rejects_invalid_ids_before_database_access():
    result = json.loads(await call_tool("read_note", {"note_id": True}, None, SimpleNamespace(id=1)))
    assert "error" in result


async def test_evaluation_uses_the_exact_production_search_payload(monkeypatch):
    from app.agent import tools
    from app.eval.pipeline import delivered_evidence
    from app.services.note_passages import build_search_payload

    notes = [Note(id=i + 1, title=f"Plan {i}", content="背景" * 2000 + " launch budget RM 42000 " + "追加" * 2000)
             for i in range(5)]

    async def retrieve(*args, **kwargs):
        return notes

    monkeypatch.setattr(tools, "search_notes", retrieve)
    production = json.loads(await tools.call_tool("search_notes", {"query": "launch budget"}, None, SimpleNamespace(id=1)))
    contexts, titles = delivered_evidence(notes, "launch budget")
    assert [json.loads(context) for context in contexts] == production["notes"]
    assert titles == [record["title"] for record in production["notes"]]
    assert production == build_search_payload(notes, "launch budget")
    assert len(contexts) < len(notes), "wide multibyte evidence must exercise aggregate truncation"
    assert "RM 42000" in contexts[0]
    assert "背景" * 2000 not in contexts[0]


def test_budgeted_evidence_survives_production_prompt_framing():
    from app.agent.budget import bound_context
    from app.agent.tools import TOOL_SCHEMAS, format_tool_result_block
    from app.services.note_passages import SEARCH_EVIDENCE_BYTES, build_search_payload

    notes = [Note(id=i, title="Plan", content="launch budget " + "🌏" * 5000) for i in range(1, 6)]
    payload = build_search_payload(notes, "launch budget")
    encoded = json.dumps(payload, ensure_ascii=False)
    assert len(encoded.encode()) <= SEARCH_EVIDENCE_BYTES
    framed = format_tool_result_block("search_notes", encoded)
    messages = [{"role": "user", "content": "launch budget"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "x", "type": "function", "function": {"name": "search_notes", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "x", "content": framed}]
    assert bound_context(messages, TOOL_SCHEMAS)[-1]["content"] == framed
