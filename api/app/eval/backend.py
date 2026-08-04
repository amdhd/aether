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
key is configured and falls back to ``OfflineBackend`` otherwise. Wrap either in
:class:`ThrottledBackend` to bound how many calls are in flight at once.

Every backend names the models it used (``generation_model``, ``judge_model``,
``embedding_model``) so the report can record them. They live here rather than
in ``EvalConfig`` because the offline backend has no models and shouldn't be made
to claim it does — it reports its own heuristics by name instead.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from typing import Protocol

from app.core import usage
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
    generation_model: str
    judge_model: str
    embedding_model: str
    embedding_dimensions: int

    async def generate_answer(self, question: str, contexts: list[str]) -> str: ...

    async def extract_claims(self, answer: str) -> list[str]: ...

    async def is_supported(self, context: str, claim: str) -> bool: ...

    async def is_relevant(self, question: str, ground_truth: str, context: str) -> bool: ...

    # Returns (reverse-generated questions, noncommittal). One method rather
    # than two because the judge decides both from the same prompt, and asking
    # twice would bill twice for one verdict.
    async def reverse_questions(self, answer: str, n: int) -> tuple[list[str], bool]: ...

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
    generation_model = "offline-extractive"
    judge_model = "offline-token-overlap"
    embedding_model = "offline-hashed-bow"
    embedding_dimensions = 256  # the `dim` in embed(); not settings.EMBEDDING_DIMENSIONS

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

    async def reverse_questions(self, answer: str, n: int) -> tuple[list[str], bool]:
        # Reverse-generation stand-in: reuse the answer's own sentences. Combined
        # with the bag-of-words embedding below, answer relevancy then reflects
        # how much the answer's content overlaps the original question.
        sentences = _split_sentences(answer) or [answer]
        low = answer.lower()
        noncommittal = any(marker in low for marker in _NONCOMMITTAL_MARKERS)
        return (sentences * n)[:n], noncommittal

    async def embed(self, text: str) -> list[float] | None:
        # Deterministic hashing bag-of-words vector, L2-normalised. Cosine
        # similarity between two of these is a real overlap signal — enough to
        # make the answer-relevancy math meaningful and testable offline.
        dim = self.embedding_dimensions
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

    def __init__(
        self, *, generation_model: str | None = None, judge_model: str | None = None
    ) -> None:
        # Imported lazily so the harness module is importable without the client
        # (and so OfflineBackend has no hard dependency on DeepSeek config).
        from app.agent.client import get_deepseek_client

        self._client = get_deepseek_client()
        # Separately overridable: judging with a stronger model than you generate
        # with is a standard eval setup, and "did the score move because the
        # generator changed or because the judge did?" is unanswerable if the two
        # are welded together.
        self.generation_model = generation_model or settings.DEEPSEEK_MODEL
        self.judge_model = judge_model or settings.DEEPSEEK_MODEL
        self.embedding_model = settings.EMBEDDING_MODEL
        self.embedding_dimensions = settings.EMBEDDING_DIMENSIONS

    async def _chat(self, model: str, messages: list[dict]) -> str:
        resp = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            stream=False,
            extra_body={"thinking": {"type": "disabled"}},
        )
        if resp.usage is not None:
            usage.record_llm(resp.usage.prompt_tokens, resp.usage.completion_tokens)
        return resp.choices[0].message.content or ""

    async def _chat_json(self, prompt: str, *, system: str | None = None) -> object:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return _parse_json(await self._chat(self.judge_model, messages))

    async def generate_answer(self, question: str, contexts: list[str]) -> str:
        context = "\n\n---\n\n".join(contexts) if contexts else "(no notes found)"
        content = await self._chat(
            self.generation_model,
            [
                {"role": "system", "content": _GENERATE_PROMPT_SYSTEM.format(context=context)},
                {"role": "user", "content": question},
            ],
        )
        return content.strip()

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

    async def reverse_questions(self, answer: str, n: int) -> tuple[list[str], bool]:
        # RAGAS folds the noncommittal verdict into question generation, and so
        # does _QUESTION_GEN_PROMPT: one reply carries both. Reading them from
        # one response also removes a way for the two to disagree.
        result = await self._chat_json(_QUESTION_GEN_PROMPT.format(n=n, answer=answer))
        if not isinstance(result, dict):
            # Unparseable reply: no questions (relevancy becomes undefined) and
            # no claim that the answer declined.
            return [], False
        raw = result.get("questions")
        questions = [str(q) for q in raw if str(q).strip()] if isinstance(raw, list) else []
        return questions, bool(result.get("noncommittal"))

    async def embed(self, text: str) -> list[float] | None:
        return await embeddings.embed_text(text)


# --- Concurrency bound ------------------------------------------------------


class ThrottledBackend:
    """Wraps a backend so at most ``limit`` of its calls are in flight at once.

    The bound is per *call*, not per sample. Bounding per sample would be a lie:
    scoring one sample fans out again internally (one ``is_supported`` call per
    claim, one ``is_relevant`` per context), so "8 samples at a time" can mean
    fifty concurrent requests. Holding the semaphore around each individual call
    is what actually caps in-flight requests at the number configured — which is
    the number that has to stay under the provider's rate limit.

    Nothing throttled here calls anything else that is throttled, so the
    semaphore is never acquired re-entrantly and cannot deadlock.
    """

    def __init__(self, inner: EvalBackend, limit: int) -> None:
        self._inner = inner
        self._sem = asyncio.Semaphore(limit)
        self.name = inner.name
        self.generation_model = inner.generation_model
        self.judge_model = inner.judge_model
        self.embedding_model = inner.embedding_model
        self.embedding_dimensions = inner.embedding_dimensions

    async def generate_answer(self, question: str, contexts: list[str]) -> str:
        async with self._sem:
            return await self._inner.generate_answer(question, contexts)

    async def extract_claims(self, answer: str) -> list[str]:
        async with self._sem:
            return await self._inner.extract_claims(answer)

    async def is_supported(self, context: str, claim: str) -> bool:
        async with self._sem:
            return await self._inner.is_supported(context, claim)

    async def is_relevant(self, question: str, ground_truth: str, context: str) -> bool:
        async with self._sem:
            return await self._inner.is_relevant(question, ground_truth, context)

    async def reverse_questions(self, answer: str, n: int) -> tuple[list[str], bool]:
        async with self._sem:
            return await self._inner.reverse_questions(answer, n)

    async def embed(self, text: str) -> list[float] | None:
        async with self._sem:
            return await self._inner.embed(text)


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


def get_backend(
    *, generation_model: str | None = None, judge_model: str | None = None
) -> EvalBackend:
    """Return the LLM backend when DeepSeek is configured, else the offline one."""
    if settings.DEEPSEEK_API_KEY:
        return LLMBackend(generation_model=generation_model, judge_model=judge_model)
    logger.warning("eval.backend.offline reason=no_deepseek_key")
    return OfflineBackend()
