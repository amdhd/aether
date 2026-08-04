"""Orchestrates a full eval run and aggregates the results.

For each golden sample: retrieve with the real retriever, generate an answer
with the backend, then score the answer/context with the three RAGAS-equivalent
metrics. Produces a structured report (per-sample rows + aggregates) that
``run.py`` renders to JSON and Markdown.

Two things the report carries besides scores, because a score on its own is not
a result you can act on:

* **The config it was produced under** (see :mod:`app.eval.config`). Two reports
  are comparable only if you can see what differed between them.
* **Cost and latency per sample.** Both are nearly free to capture — the token
  counts come back on the responses the harness is already making — and a
  retrieval config that buys +0.02 faithfulness for 3x the tokens is a bad trade
  you cannot spot from scores alone.

Execution is two-phase. Retrieval runs first and sequentially: it shares one
``AsyncSession``, which is not safe for concurrent use, and the default
in-memory SQLite engine is a single connection besides. Generation and judging
then run for all samples concurrently, because that phase is where the LLM round
trips are — one generation plus roughly a dozen judge calls per sample — and it
is entirely network-bound. In-flight requests are bounded by
:class:`~app.eval.backend.ThrottledBackend` rather than left to fan out freely,
so a large golden set doesn't walk straight into the provider's rate limit.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import metrics as core_metrics
from app.core.usage import TokenUsage, meter
from app.eval import metrics
from app.eval.backend import EvalBackend, ThrottledBackend
from app.eval.config import EvalConfig
from app.eval.dataset import CORPUS, GOLDEN, EvalSample
from app.eval.pipeline import note_text, retrieve, seed_corpus

# Corpus titles are unique, so title -> slug recovers which note was retrieved
# without threading a slug column through the ORM model.
_TITLE_TO_SLUG = {note.title: note.slug for note in CORPUS}


@dataclass
class SampleTimings:
    """Wall time in milliseconds for one sample, split by phase."""

    retrieval: float = 0.0
    generation: float = 0.0
    judging: float = 0.0

    @property
    def total(self) -> float:
        return self.retrieval + self.generation + self.judging


@dataclass
class SampleResult:
    question: str
    ground_truth: str
    answer: str
    relevant_slugs: list[str]
    retrieved_slugs: list[str]
    answerable: bool
    faithfulness: float | None
    context_precision: float | None
    answer_relevancy: float | None
    # Did retrieval surface every note the answer needs? (Answerable samples only.)
    retrieval_recall: float | None
    timings_ms: SampleTimings = field(default_factory=SampleTimings)
    usage: TokenUsage = field(default_factory=TokenUsage)
    estimated_cost_usd: float = 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        # asdict() drops properties, and both of these are ones a reader wants.
        data["timings_ms"]["total"] = self.timings_ms.total
        data["usage"] = self.usage.to_dict()
        return data


@dataclass
class EvalReport:
    backend: str
    generated_at: str
    n_samples: int
    aggregates: dict[str, float | None]
    samples: list[SampleResult] = field(default_factory=list)
    # Defaulted so a report can still be built from aggregates alone, which is
    # what the gate tests do.
    config: dict[str, object] = field(default_factory=dict)
    totals: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "generated_at": self.generated_at,
            "n_samples": self.n_samples,
            "config": self.config,
            "aggregates": self.aggregates,
            "totals": self.totals,
            "samples": [s.to_dict() for s in self.samples],
        }


@dataclass
class _Retrieved:
    """Phase-1 output: what phase 2 needs, with no ORM objects left in it.

    Contexts and slugs are materialised as plain strings while the session is
    still open, so phase 2 — which runs long, after the session has closed —
    never touches a detached instance.
    """

    sample: EvalSample
    contexts: list[str]
    retrieved_slugs: list[str]
    usage: TokenUsage
    retrieval_ms: float


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present) / len(present)


def _percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile. Golden sets are small, and interpolating would
    imply a precision the sample size doesn't support."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), -(-int(pct) * len(ordered) // 100)))
    return ordered[rank - 1]


async def _score_sample(
    backend: EvalBackend, config: EvalConfig, sample: EvalSample, answer: str, contexts: list[str]
) -> tuple[float | None, float | None, float | None]:
    # The three metrics are independent, so run them together rather than paying
    # three serial round trips per sample. ThrottledBackend keeps the resulting
    # fan-out inside the configured in-flight bound.
    faith, precision, relevancy = await asyncio.gather(
        metrics.faithfulness(backend, answer, contexts),
        metrics.context_precision(backend, sample.question, sample.ground_truth, contexts),
        metrics.answer_relevancy(
            backend, sample.question, answer, n_questions=config.relevancy_questions
        ),
    )
    return faith, precision, relevancy


async def _generate_and_score(
    backend: EvalBackend, config: EvalConfig, item: _Retrieved
) -> SampleResult:
    sample = item.sample
    # Keep accumulating into the counter phase 1 started, so one row's tokens
    # cover its retrieval embedding as well as its generation and judging.
    with meter(into=item.usage):
        started = time.perf_counter()
        answer = await backend.generate_answer(sample.question, item.contexts)
        generation_ms = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        faith, precision, relevancy = await _score_sample(
            backend, config, sample, answer, item.contexts
        )
        judging_ms = (time.perf_counter() - started) * 1000

    answerable = bool(sample.relevant_slugs)
    recall: float | None = None
    if answerable:
        hit = sum(1 for s in sample.relevant_slugs if s in item.retrieved_slugs)
        recall = hit / len(sample.relevant_slugs)

    return SampleResult(
        question=sample.question,
        ground_truth=sample.ground_truth,
        answer=answer,
        relevant_slugs=sample.relevant_slugs,
        retrieved_slugs=item.retrieved_slugs,
        answerable=answerable,
        faithfulness=faith,
        context_precision=precision,
        answer_relevancy=relevancy,
        retrieval_recall=recall,
        timings_ms=SampleTimings(
            retrieval=item.retrieval_ms, generation=generation_ms, judging=judging_ms
        ),
        usage=item.usage,
        estimated_cost_usd=core_metrics.estimate_cost_usd(
            item.usage.prompt_tokens, item.usage.completion_tokens, item.usage.embedding_tokens
        ),
    )


async def _retrieve_all(session_factory: async_sessionmaker, config: EvalConfig) -> list[_Retrieved]:
    items: list[_Retrieved] = []
    async with session_factory() as db:
        user = await seed_corpus(db)
        for sample in GOLDEN:
            usage = TokenUsage()
            started = time.perf_counter()
            with meter(into=usage):
                notes = await retrieve(db, user, sample.question, limit=config.retrieval_k)
            retrieval_ms = (time.perf_counter() - started) * 1000
            items.append(
                _Retrieved(
                    sample=sample,
                    contexts=[note_text(n) for n in notes],
                    retrieved_slugs=[_TITLE_TO_SLUG.get(n.title, n.title) for n in notes],
                    usage=usage,
                    retrieval_ms=retrieval_ms,
                )
            )
    return items


async def run_eval(
    session_factory: async_sessionmaker,
    backend: EvalBackend,
    config: EvalConfig | None = None,
) -> EvalReport:
    config = config or EvalConfig()
    throttled = ThrottledBackend(backend, config.concurrency)

    wall_started = time.perf_counter()
    retrieved = await _retrieve_all(session_factory, config)
    # gather preserves argument order, so per-sample rows stay aligned with the
    # golden set regardless of which samples happen to finish first.
    results = list(
        await asyncio.gather(*(_generate_and_score(throttled, config, item) for item in retrieved))
    )
    wall_seconds = time.perf_counter() - wall_started

    aggregates: dict[str, float | None] = {
        "faithfulness": _mean([r.faithfulness for r in results]),
        "context_precision": _mean([r.context_precision for r in results]),
        "answer_relevancy": _mean([r.answer_relevancy for r in results]),
        "retrieval_recall": _mean([r.retrieval_recall for r in results]),
    }

    total_usage = TokenUsage()
    for r in results:
        total_usage.merge(r.usage)
    latencies = [r.timings_ms.total for r in results]
    total_cost = round(sum(r.estimated_cost_usd for r in results), 8)

    return EvalReport(
        backend=throttled.name,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        n_samples=len(results),
        aggregates=aggregates,
        samples=results,
        config={
            **config.to_dict(),
            "generation_model": throttled.generation_model,
            "judge_model": throttled.judge_model,
            "embedding_model": throttled.embedding_model,
            "embedding_dimensions": throttled.embedding_dimensions,
        },
        totals={
            # Wall time is the whole run; the latency stats are per sample. They
            # differ by roughly the concurrency factor, which is the point.
            "wall_seconds": round(wall_seconds, 3),
            "sample_latency_ms_mean": _mean(list(latencies)),
            "sample_latency_ms_p50": _percentile(latencies, 50),
            "sample_latency_ms_p95": _percentile(latencies, 95),
            "usage": total_usage.to_dict(),
            "estimated_cost_usd": total_cost,
            "estimated_cost_usd_per_sample": (
                round(total_cost / len(results), 8) if results else 0.0
            ),
        },
    )
