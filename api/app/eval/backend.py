"""Pluggable backends for the eval harness.

An ``EvalBackend`` supplies the two LLM-dependent capabilities the harness
needs: *generation* (answer a question from retrieved context) and *judging*
(the small classification/decomposition steps the RAGAS metrics are built out
of — claim extraction, entailment, relevance verdicts, question generation,
embeddings).

There are two implementations:

* ``LLMBackend`` — the real thing. Generation and judging run on the same
  DeepSeek model the app uses; embeddings use the same OpenAI model as
  production note search. This is what you run to get meaningful numbers.

* ``OfflineBackend`` — deterministic, keyless token-overlap heuristics. It lets
  the harness (and the metric math) run in CI with no API keys, mirroring the
  rest of this codebase's "degrade gracefully without a key" design. Its scores
  are not authoritative — they exist so the plumbing is testable and the
  harness produces *something* in a keyless demo.

Pick one with :func:`get_backend`, which chooses ``LLMBackend`` when a DeepSeek
key is configured and falls back to ``OfflineBackend`` otherwise.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Protocol

from app.core.config import settings
from app.core.logging import get_logger
from app.services import embeddings

logger = get_logger(__name__)

_WORD_RE = re.compile(r"[a-z0-9]+")
# Words too common to carry meaning in the offline overlap heuristics.
_STOPWORDS = frozenset(
    "a an the is are was were be been being do does did to of in on at for and or "
    "not no yes it its this that these those i you he she they we my your his her "
    "their our what when where why how which who whom with without from by as into "
    "about over under again then than so if but can could should would will shall "
    "may might must have has had am".split()
)


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS]


class EvalBackend(Protocol):
    name: str

    async def generate_answer(self, question: str, contexts: list[str]) -> str: ...

    async def extract_claims(self, answer: str) -> list[str]: ...

    async def is_supported(self, context: str, claim: str) -> bool: ...

    async def is_relevant(self, question: str, ground_truth: str, context: str) -> bool: ...

    async def generate_questions(self, answer: str, n: int) -> list[str]: ...

    async def is_noncommittal(self, answer: str) -> bool: ...

    async def embed(self, text: str) -> list[float] | None: ...


# --- Offline (deterministic, keyless) ---------------------------------------

_NONCOMMITTAL_MARKERS = (
    "i don't know",
    "i do not know",
    "not sure",
    "no information",
    "do not contain",
    "does not contain",
    "don't have",
    "do not have",
    "cannot answer",
    "can't answer",
    "no mention",
    "not recorded",
    "not in the notes",
)


class OfflineBackend:
    """Deterministic heuristics — no network, no keys. Used for CI and tests."""

    name = "offline"

    async def generate_answer(self, question: str, contexts: list[str]) -> str:
        # Extractive stand-in for a generator: return the sentences from the
        # retrieved context that share the most words with the question. With no
        # relevant context, decline — so the "unanswerable" samples exercise the
        # same declining behaviour we expect from the real model.
        q = set(_tokens(question))
        scored: list[tuple[int, str]] = []
        for context in contexts:
            for sentence in _split_sentences(context):
                overlap = len(q & set(_tokens(sentence)))
                if overlap:
                    scored.append((overlap, sentence))
        if not scored:
            return "The notes do not contain an answer to that."
        scored.sort(key=lambda s: s[0], reverse=True)
        return " ".join(sentence for _, sentence in scored[:2])

    async def extract_claims(self, answer: str) -> list[str]:
        return _split_sentences(answer)

    async def is_supported(self, context: str, claim: str) -> bool:
        # "Entailment" ~ most of the claim's content words appear in the context.
        claim_words = set(_tokens(claim))
        if not claim_words:
            return True
        context_words = set(_tokens(context))
        return len(claim_words & context_words) / len(claim_words) >= 0.6

    async def is_relevant(self, question: str, ground_truth: str, context: str) -> bool:
        # A context is "useful" if it overlaps the reference answer's content.
        gt_words = set(_tokens(ground_truth))
        if not gt_words:
            return False
        context_words = set(_tokens(context))
        return len(gt_words & context_words) / len(gt_words) >= 0.3

    async def generate_questions(self, answer: str, n: int) -> list[str]:
        # Reverse-generation stand-in: reuse the answer's own sentences. Combined
        # with the bag-of-words embedding below, answer relevancy then reflects
        # how much the answer's content overlaps the original question.
        sentences = _split_sentences(answer) or [answer]
        return (sentences * n)[:n]

    async def is_noncommittal(self, answer: str) -> bool:
        low = answer.lower()
        return any(marker in low for marker in _NONCOMMITTAL_MARKERS)

    async def embed(self, text: str) -> list[float] | None:
        # Deterministic hashing bag-of-words vector, L2-normalised. Cosine
        # similarity between two of these is a real overlap signal — enough to
        # make the answer-relevancy math meaningful and testable offline.
        dim = 256
        vec = [0.0] * dim
        for token in _tokens(text):
            h = int(hashlib.md5(token.encode()).hexdigest(), 16)
            vec[h % dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            return None
        return [v / norm for v in vec]


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


# --- LLM-backed (DeepSeek judge + OpenAI embeddings) ------------------------

_CLAIM_PROMPT = (
    "Break the following answer into a list of standalone factual claims. Each "
    "claim must be atomic (one fact) and understandable without the others. "
    "Reply with ONLY a JSON array of strings.\n\nAnswer:\n{answer}"
)

_SUPPORT_PROMPT = (
    "Can the CLAIM be directly inferred from the CONTEXT? Answer only about "
    "logical support, not real-world truth. Reply with ONLY a JSON object "
    '{{"supported": true|false}}.\n\nCONTEXT:\n{context}\n\nCLAIM:\n{claim}'
)

_RELEVANCE_PROMPT = (
    "Given the QUESTION and the REFERENCE ANSWER, is the CONTEXT useful for "
    "arriving at the reference answer? Reply with ONLY a JSON object "
    '{{"useful": true|false}}.\n\nQUESTION:\n{question}\n\nREFERENCE ANSWER:\n'
    "{ground_truth}\n\nCONTEXT:\n{context}"
)

_QUESTION_GEN_PROMPT = (
    "Generate {n} distinct questions that the following answer would be a "
    "complete and direct response to. If the answer is evasive or says it does "
    'not know, set noncommittal to true. Reply with ONLY a JSON object '
    '{{"questions": [...], "noncommittal": true|false}}.\n\nAnswer:\n{answer}'
)

_GENERATE_PROMPT_SYSTEM = (
    "You are a personal assistant answering the user's question using ONLY the "
    "context from their notes below. If the notes do not contain the answer, "
    "say so plainly and do not guess. Be concise.\n\nNOTES:\n{context}"
)


class LLMBackend:
    """DeepSeek for generation and judging; OpenAI embeddings for relevancy."""

    name = "llm"

    def __init__(self) -> None:
        # Imported lazily so the harness module is importable without the client
        # (and so OfflineBackend has no hard dependency on DeepSeek config).
        from app.agent.client import get_deepseek_client

        self._client = get_deepseek_client()

    async def _chat_json(self, prompt: str, *, system: str | None = None) -> object:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = await self._client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=messages,
            stream=False,
            extra_body={"thinking": {"type": "disabled"}},
        )
        content = resp.choices[0].message.content or ""
        return _parse_json(content)

    async def generate_answer(self, question: str, contexts: list[str]) -> str:
        context = "\n\n---\n\n".join(contexts) if contexts else "(no notes found)"
        resp = await self._client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": _GENERATE_PROMPT_SYSTEM.format(context=context)},
                {"role": "user", "content": question},
            ],
            stream=False,
            extra_body={"thinking": {"type": "disabled"}},
        )
        return (resp.choices[0].message.content or "").strip()

    async def extract_claims(self, answer: str) -> list[str]:
        result = await self._chat_json(_CLAIM_PROMPT.format(answer=answer))
        if isinstance(result, list):
            return [str(c) for c in result if str(c).strip()]
        return []

    async def is_supported(self, context: str, claim: str) -> bool:
        result = await self._chat_json(_SUPPORT_PROMPT.format(context=context, claim=claim))
        return bool(isinstance(result, dict) and result.get("supported"))

    async def is_relevant(self, question: str, ground_truth: str, context: str) -> bool:
        result = await self._chat_json(
            _RELEVANCE_PROMPT.format(question=question, ground_truth=ground_truth, context=context)
        )
        return bool(isinstance(result, dict) and result.get("useful"))

    async def generate_questions(self, answer: str, n: int) -> list[str]:
        result = await self._chat_json(_QUESTION_GEN_PROMPT.format(n=n, answer=answer))
        if isinstance(result, dict) and isinstance(result.get("questions"), list):
            return [str(q) for q in result["questions"] if str(q).strip()]
        return []

    async def is_noncommittal(self, answer: str) -> bool:
        # RAGAS folds this into question generation; ask the same call.
        result = await self._chat_json(_QUESTION_GEN_PROMPT.format(n=1, answer=answer))
        return bool(isinstance(result, dict) and result.get("noncommittal"))

    async def embed(self, text: str) -> list[float] | None:
        return await embeddings.embed_text(text)


def _parse_json(content: str) -> object:
    """Best-effort JSON extraction from a model reply.

    Models wrap JSON in prose or ```json fences despite instructions; try a
    clean parse first, then the first {...}/[...] span. Returns ``None`` when
    nothing parses so callers degrade to a conservative default rather than
    crashing the whole run on one malformed reply.
    """
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\{.*\}|\[.*\])", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    logger.warning("eval.judge.unparseable_json content=%r", content[:200])
    return None


def get_backend() -> EvalBackend:
    """Return the LLM backend when DeepSeek is configured, else the offline one."""
    if settings.DEEPSEEK_API_KEY:
        return LLMBackend()
    logger.warning("eval.backend.offline reason=no_deepseek_key")
    return OfflineBackend()
