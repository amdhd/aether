"""Unit tests for the RAGAS-equivalent metric math.

These pin the formulas with a hand-controlled fake backend so the arithmetic is
verified independently of any LLM: faithfulness as a supported-claim fraction,
context precision as rank-weighted average precision, answer relevancy as mean
cosine similarity, and the noncommittal short-circuit.
"""

import math

import pytest

from app.eval import metrics


class FakeBackend:
    """A scripted backend: every judgement is dictated by the test, not inferred."""

    name = "fake"

    def __init__(
        self,
        *,
        claims: list[str] | None = None,
        supported: dict[str, bool] | None = None,
        relevant: list[bool] | None = None,
        questions: list[str] | None = None,
        noncommittal: bool = False,
        vectors: dict[str, list[float]] | None = None,
    ) -> None:
        self._claims = claims or []
        self._supported = supported or {}
        self._relevant = relevant or []
        self._questions = questions or []
        self._noncommittal = noncommittal
        self._vectors = vectors or {}
        self._relevant_calls = 0

    async def generate_answer(self, question, contexts):  # pragma: no cover - unused here
        return ""

    async def extract_claims(self, answer):
        return self._claims

    async def is_supported(self, context, claim):
        return self._supported.get(claim, False)

    async def is_relevant(self, question, ground_truth, context):
        # Verdicts are positional: the nth context uses the nth scripted bool.
        verdict = self._relevant[self._relevant_calls]
        self._relevant_calls += 1
        return verdict

    async def generate_questions(self, answer, n):
        return self._questions

    async def is_noncommittal(self, answer):
        return self._noncommittal

    async def embed(self, text):
        return self._vectors.get(text)


async def test_faithfulness_is_supported_fraction() -> None:
    backend = FakeBackend(
        claims=["a", "b", "c", "d"],
        supported={"a": True, "b": True, "c": False, "d": True},
    )
    score = await metrics.faithfulness(backend, "answer", ["ctx"])
    assert score == pytest.approx(3 / 4)


async def test_faithfulness_undefined_without_claims() -> None:
    backend = FakeBackend(claims=[])
    assert await metrics.faithfulness(backend, "", ["ctx"]) is None


async def test_context_precision_rewards_early_relevant_ranks() -> None:
    # Relevant, irrelevant, relevant -> AP = (1/1 + 2/3) / 2 = 0.8333...
    backend = FakeBackend(relevant=[True, False, True])
    score = await metrics.context_precision(backend, "q", "gt", ["c1", "c2", "c3"])
    assert score == pytest.approx((1.0 + 2 / 3) / 2)


async def test_context_precision_penalises_late_relevant_ranks() -> None:
    # Same relevant count, worse ranking: irrelevant, relevant -> AP = (1/2)/1 = 0.5
    backend = FakeBackend(relevant=[False, True])
    score = await metrics.context_precision(backend, "q", "gt", ["c1", "c2"])
    assert score == pytest.approx(0.5)


async def test_context_precision_zero_when_nothing_relevant() -> None:
    backend = FakeBackend(relevant=[False, False])
    assert await metrics.context_precision(backend, "q", "gt", ["c1", "c2"]) == 0.0


async def test_context_precision_undefined_when_nothing_retrieved() -> None:
    backend = FakeBackend(relevant=[])
    assert await metrics.context_precision(backend, "q", "gt", []) is None


async def test_answer_relevancy_is_mean_cosine() -> None:
    # Question vector vs two generated-question vectors: cos(0)=1, cos(90deg)=0
    # -> mean 0.5.
    backend = FakeBackend(
        questions=["g1", "g2"],
        vectors={"q": [1.0, 0.0], "g1": [1.0, 0.0], "g2": [0.0, 1.0]},
    )
    score = await metrics.answer_relevancy(backend, "q", "answer")
    assert score == pytest.approx(0.5)


async def test_answer_relevancy_zero_when_noncommittal() -> None:
    backend = FakeBackend(noncommittal=True)
    assert await metrics.answer_relevancy(backend, "q", "I don't know") == 0.0


async def test_answer_relevancy_undefined_without_embeddings() -> None:
    # Embeddings disabled (embed returns None for everything).
    backend = FakeBackend(questions=["g1"], vectors={})
    assert await metrics.answer_relevancy(backend, "q", "answer") is None


def test_cosine_matches_manual_computation() -> None:
    assert metrics._cosine([1.0, 2.0], [2.0, 4.0]) == pytest.approx(1.0)
    assert metrics._cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert metrics._cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
    assert metrics._cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    # Sanity: a normalized vector against itself is 1.
    v = [0.6, 0.8]
    assert metrics._cosine(v, v) == pytest.approx(1.0)
    assert math.isclose(sum(x * x for x in v), 1.0)
