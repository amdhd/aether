"""Conservative input budget shared by chat and summarization.

Count each serialized UTF-8 byte as one token, plus framing headroom. This
intentionally overestimates byte-tokenized model input instead of relying on
an English-only chars/4 estimate. Includes tool schemas and reasoning text.
"""
import json
from typing import Any

INPUT_TOKEN_BUDGET = 64_000
MAX_HISTORY_ROWS = 200
MAX_TOOL_RESULT_BYTES = 12_000


class ContextBudgetExceeded(ValueError):
    pass


def clip_text(text: str, byte_limit: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= byte_limit:
        return text
    suffix = "\n[Truncated to fit the context budget]"
    return raw[:max(0, byte_limit - len(suffix.encode()))].decode("utf-8", errors="ignore") + suffix


def input_token_upper_bound(messages: list[dict[str, Any]], tools: list | None = None) -> int:
    payload = json.dumps({"messages": messages, "tools": tools or []}, ensure_ascii=False)
    return len(payload.encode("utf-8")) + 64 * len(messages) + 1024


def bound_context(messages: list[dict[str, Any]], tools: list | None = None) -> list[dict[str, Any]]:
    # Copy rather than changing persisted history. Retain tool-call structures
    # verbatim: truncating argument JSON or dropping one result corrupts a call.
    bounded = []
    for message in messages:
        item = dict(message)
        for field, limit in (("content", 12_000), ("reasoning_content", 4_000)):
            if isinstance(item.get(field), str):
                item[field] = clip_text(item[field], limit)
        bounded.append(item)
    systems = [item for item in bounded if item["role"] == "system"]
    groups: list[list[dict[str, Any]]] = []
    for item in bounded:
        if item["role"] == "system":
            continue
        if item["role"] == "user" or not groups:
            groups.append([])
        groups[-1].append(item)
    # Remove complete oldest turns, preserving assistant/tool-result pairs.
    while len(groups) > 1 and input_token_upper_bound(systems + [m for g in groups for m in g], tools) > INPUT_TOKEN_BUDGET:
        groups.pop(0)
    result = systems + [m for group in groups for m in group]
    if input_token_upper_bound(result, tools) > INPUT_TOKEN_BUDGET:
        raise ContextBudgetExceeded("This turn exceeds the AI context budget. Start a new chat with a smaller request.")
    return result


def bounded_tool_result(result: dict[str, Any]) -> str:
    encoded = json.dumps(result, ensure_ascii=False)
    if len(encoded.encode("utf-8")) <= MAX_TOOL_RESULT_BYTES:
        return encoded
    # Keep valid JSON and never splice an incomplete record. Paginated lists
    # retain a cursor to the first omitted record rather than skipping it.
    for key in ("notes", "tasks", "events", "results"):
        rows = result.get(key)
        if isinstance(rows, list):
            result = dict(result)
            result[key] = list(rows)
            result["truncated"] = True
            while result[key]:
                result[key].pop()
                if key in ("notes", "tasks") and result[key]:
                    result["next_before_id"] = result[key][-1]["id"]
                if not result[key]:
                    break
                encoded = json.dumps(result, ensure_ascii=False)
                if len(encoded.encode("utf-8")) <= MAX_TOOL_RESULT_BYTES:
                    return encoded
    return json.dumps({"error": "Tool result exceeds the size limit; request fewer or smaller records."})
