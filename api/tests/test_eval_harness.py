"""End-to-end smoke test for the eval harness on the offline backend.

Runs the whole pipeline — seed corpus, retrieve with the real ``search_notes``,
generate, score — against in-memory SQLite with no API keys, and asserts the
report is well-formed and the metrics land in range. This is the CI guard that
the harness plumbing (not just the metric math) stays wired together.
"""

import pytest

from app.eval.backend import OfflineBackend
from app.eval.dataset import CORPUS, GOLDEN
from app.eval.harness import run_eval
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


def test_golden_slugs_reference_real_corpus_notes() -> None:
    # Guard against a dataset typo silently making a sample unanswerable.
    slugs = {note.slug for note in CORPUS}
    for sample in GOLDEN:
        for slug in sample.relevant_slugs:
            assert slug in slugs, f"unknown slug {slug!r} in golden dataset"
