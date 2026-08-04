"""End-to-end tests for the eval harness on the offline backend.

Runs the whole pipeline — seed corpus, retrieve with the real ``search_notes``,
generate, score — against in-memory SQLite with no API keys, and asserts the
report is well-formed and the metrics land in range. This is the CI guard that
the harness plumbing (not just the metric math) stays wired together.

Also covers the three properties that make a run *useful* rather than merely
correct: the config knobs reach the code they name and are recorded in the
report, every sample carries its own tokens/cost/latency, and scoring runs
concurrently inside the configured in-flight bound.
"""

import asyncio

import pytest
from sqlalchemy import func, select

from app.core import usage
from app.core.usage import TokenUsage
from app.eval.backend import OfflineBackend
from app.eval.config import EvalConfig
from app.eval.dataset import CORPUS, GOLDEN
from app.eval.harness import run_eval
from app.eval.pipeline import seed_corpus
from app.models.note import Note
from app.models.user import User
from tests.conftest import TestingSessionLocal


async def test_offline_harness_runs_end_to_end() -> None:
    report = await run_eval(TestingSessionLocal, OfflineBackend())

    assert report.backend == "offline"
    assert report.n_samples == len(GOLDEN)
    assert len(report.samples) == len(GOLDEN)

    for key in ("faithfulness", "context_precision", "answer_relevancy", "retrieval_recall"):
        value = report.aggregates[key]
        assert value is None or 0.0 <= value <= 1.0

    # Every per-sample metric is either undefined or a valid probability.
    for sample in report.samples:
        for value in (sample.faithfulness, sample.context_precision, sample.answer_relevancy):
            assert value is None or 0.0 <= value <= 1.0


async def test_harness_retrieves_relevant_notes_for_answerable_samples() -> None:
    # With the term-based keyword fallback, retrieval should surface the labelled
    # relevant note for answerable samples — i.e. recall is perfect here, which
    # is what lets the downstream metrics be meaningful on the offline backend.
    report = await run_eval(TestingSessionLocal, OfflineBackend())
    answerable = [s for s in report.samples if s.answerable]
    assert answerable
    assert all(s.retrieval_recall == pytest.approx(1.0) for s in answerable)


async def test_seed_corpus_is_idempotent() -> None:
    # Re-seeding a persistent DB must not collide on the unique eval email or
    # pile up duplicate notes — it clears the prior corpus and starts clean.
    async with TestingSessionLocal() as db:
        await seed_corpus(db)
        await seed_corpus(db)  # would raise on duplicate email before the fix
        users = await db.scalar(select(func.count()).select_from(User))
        notes = await db.scalar(select(func.count()).select_from(Note))
        assert users == 1
        assert notes == len(CORPUS)


def test_golden_slugs_reference_real_corpus_notes() -> None:
    # Guard against a dataset typo silently making a sample unanswerable.
    slugs = {note.slug for note in CORPUS}
    for sample in GOLDEN:
        for slug in sample.relevant_slugs:
            assert slug in slugs, f"unknown slug {slug!r} in golden dataset"


# --- Configuration ----------------------------------------------------------


async def test_retrieval_k_is_applied_and_changes_what_is_retrieved() -> None:
    # The knob has to actually reach the retriever — a config field that is
    # recorded but not honoured is worse than no field at all, because the
    # report would then attribute a score to a setting that never took effect.
    narrow = await run_eval(TestingSessionLocal, OfflineBackend(), EvalConfig(retrieval_k=1))
    wide = await run_eval(TestingSessionLocal, OfflineBackend(), EvalConfig(retrieval_k=5))

    assert all(len(s.retrieved_slugs) <= 1 for s in narrow.samples)
    assert all(len(s.retrieved_slugs) <= 5 for s in wide.samples)
    assert sum(len(s.retrieved_slugs) for s in wide.samples) > sum(
        len(s.retrieved_slugs) for s in narrow.samples
    )


async def test_report_records_the_config_it_ran_under() -> None:
    # Two reports are only comparable if each says what it was produced with.
    config = EvalConfig(retrieval_k=3, relevancy_questions=2, concurrency=4)
    report = await run_eval(TestingSessionLocal, OfflineBackend(), config)

    assert report.config["retrieval_k"] == 3
    assert report.config["relevancy_questions"] == 2
    assert report.config["concurrency"] == 4
    # Model identity comes off the backend, so the offline run must not claim to
    # have used the production models or embedding width.
    assert report.config["generation_model"] == OfflineBackend.generation_model
    assert report.config["judge_model"] == OfflineBackend.judge_model
    assert report.config["embedding_dimensions"] == OfflineBackend.embedding_dimensions
    assert report.config["pricing_usd_per_1m"]["input"] > 0
    # And it survives the JSON round trip the reports are written through.
    assert report.to_dict()["config"] == report.config


@pytest.mark.parametrize("bad", [{"retrieval_k": 0}, {"relevancy_questions": 0}, {"concurrency": 0}])
def test_config_rejects_nonsense_values(bad: dict) -> None:
    with pytest.raises(ValueError):
        EvalConfig(**bad)


# --- Cost and latency -------------------------------------------------------


class _MeteredBackend(OfflineBackend):
    """Offline scoring, but reports token usage the way a real provider would."""

    def __init__(self, prompt: int = 10, completion: int = 4) -> None:
        self._prompt = prompt
        self._completion = completion

    async def generate_answer(self, question: str, contexts: list[str]) -> str:
        usage.record_llm(self._prompt, self._completion)
        return await super().generate_answer(question, contexts)

    async def is_supported(self, context: str, claim: str) -> bool:
        usage.record_llm(self._prompt, self._completion)
        return await super().is_supported(context, claim)

    async def embed(self, text: str) -> list[float] | None:
        usage.record_embedding(7)
        return await super().embed(text)


async def test_each_sample_carries_its_own_tokens_cost_and_latency() -> None:
    report = await run_eval(TestingSessionLocal, _MeteredBackend(), EvalConfig(concurrency=4))

    for sample in report.samples:
        assert sample.usage.llm_calls > 0
        assert sample.usage.prompt_tokens > 0
        assert sample.usage.embedding_tokens > 0
        assert sample.usage.total_tokens == (
            sample.usage.prompt_tokens
            + sample.usage.completion_tokens
            + sample.usage.embedding_tokens
        )
        assert sample.estimated_cost_usd > 0
        assert sample.timings_ms.total > 0
        assert sample.timings_ms.total == pytest.approx(
            sample.timings_ms.retrieval
            + sample.timings_ms.generation
            + sample.timings_ms.judging
        )


async def test_totals_are_the_sum_of_the_per_sample_rows() -> None:
    # Concurrent samples share one contextvar-based meter mechanism; this is the
    # guard that usage lands on the sample that caused it rather than pooling.
    report = await run_eval(TestingSessionLocal, _MeteredBackend(), EvalConfig(concurrency=4))
    totals = report.totals["usage"]

    assert totals["llm_calls"] == sum(s.usage.llm_calls for s in report.samples)
    assert totals["prompt_tokens"] == sum(s.usage.prompt_tokens for s in report.samples)
    assert totals["embedding_tokens"] == sum(s.usage.embedding_tokens for s in report.samples)
    assert report.totals["estimated_cost_usd"] == pytest.approx(
        sum(s.estimated_cost_usd for s in report.samples)
    )
    assert report.totals["estimated_cost_usd_per_sample"] == pytest.approx(
        report.totals["estimated_cost_usd"] / report.n_samples
    )
    # Every sample is distinctly metered, not handed a shared counter.
    assert len({id(s.usage) for s in report.samples}) == report.n_samples


async def test_offline_backend_reports_no_spend() -> None:
    # Nothing was billed, so the cost column must say so rather than quietly
    # pricing heuristic "calls" as if they hit a provider.
    report = await run_eval(TestingSessionLocal, OfflineBackend())
    assert report.totals["usage"]["total_tokens"] == 0
    assert report.totals["estimated_cost_usd"] == 0.0


# --- Concurrency ------------------------------------------------------------


class _ConcurrencyProbe(OfflineBackend):
    """Records the high-water mark of simultaneously in-flight backend calls."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0

    async def _tracked(self, coro):
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            # Yield to the loop so overlapping calls are actually observable.
            await asyncio.sleep(0.005)
            return await coro
        finally:
            self.in_flight -= 1

    async def generate_answer(self, question: str, contexts: list[str]) -> str:
        return await self._tracked(super().generate_answer(question, contexts))

    async def is_supported(self, context: str, claim: str) -> bool:
        return await self._tracked(super().is_supported(context, claim))

    async def is_relevant(self, question: str, ground_truth: str, context: str) -> bool:
        return await self._tracked(super().is_relevant(question, ground_truth, context))


async def test_samples_are_scored_concurrently_within_the_configured_bound() -> None:
    probe = _ConcurrencyProbe()
    await run_eval(TestingSessionLocal, probe, EvalConfig(concurrency=3))

    # Bounded: never more in flight than the provider's rate limit allows for.
    assert probe.peak <= 3
    # ...but genuinely concurrent. Serial execution — the bug this replaces —
    # would leave the peak at 1 and the assertion above trivially satisfied.
    assert probe.peak > 1


async def test_concurrency_one_still_produces_identical_scores() -> None:
    # Concurrency is a scheduling change, not a semantic one: the same golden set
    # through the same deterministic backend must score the same either way.
    serial = await run_eval(TestingSessionLocal, OfflineBackend(), EvalConfig(concurrency=1))
    parallel = await run_eval(TestingSessionLocal, OfflineBackend(), EvalConfig(concurrency=8))

    assert serial.aggregates == parallel.aggregates
    # Row order follows the golden set regardless of completion order.
    assert [s.question for s in serial.samples] == [s.question for s in parallel.samples]


async def test_usage_meter_is_inert_outside_a_run() -> None:
    # The meter is opt-in: production request paths that never open one must not
    # pay for it or accumulate anywhere global.
    assert usage.current() is None
    usage.record_llm(100, 100)
    assert usage.current() is None

    with usage.meter() as scoped:
        usage.record_llm(3, 2)
        usage.record_embedding(5)
    assert scoped == TokenUsage(
        llm_calls=1, prompt_tokens=3, completion_tokens=2, embedding_calls=1, embedding_tokens=5
    )
    assert usage.current() is None
