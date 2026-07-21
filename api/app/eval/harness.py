"""Orchestrates a full eval run and aggregates the results.

For each golden sample: retrieve with the real retriever, generate an answer
with the backend, then score the answer/context with the three RAGAS-equivalent
metrics. Produces a structured report (per-sample rows + aggregates) that
``run.py`` renders to JSON and Markdown.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.eval import metrics
from app.eval.backend import EvalBackend
from app.eval.dataset import CORPUS, GOLDEN, EvalSample
from app.eval.pipeline import RETRIEVAL_K, note_text, retrieve, seed_corpus

# Corpus titles are unique, so title -> slug recovers which note was retrieved
# without threading a slug column through the ORM model.
_TITLE_TO_SLUG = {note.title: note.slug for note in CORPUS}


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


@dataclass
class EvalReport:
    backend: str
    generated_at: str
    n_samples: int
    aggregates: dict[str, float | None]
    samples: list[SampleResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "generated_at": self.generated_at,
            "n_samples": self.n_samples,
            "aggregates": self.aggregates,
            "samples": [asdict(s) for s in self.samples],
        }


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present) / len(present)


async def _score_sample(
    backend: EvalBackend, sample: EvalSample, answer: str, contexts: list[str]
) -> tuple[float | None, float | None, float | None]:
    faith = await metrics.faithfulness(backend, answer, contexts)
    precision = await metrics.context_precision(
        backend, sample.question, sample.ground_truth, contexts
    )
    relevancy = await metrics.answer_relevancy(backend, sample.question, answer)
    return faith, precision, relevancy


async def run_eval(session_factory: async_sessionmaker, backend: EvalBackend) -> EvalReport:
    async with session_factory() as db:
        user = await seed_corpus(db)

        results: list[SampleResult] = []
        for sample in GOLDEN:
            notes = await retrieve(db, user, sample.question, limit=RETRIEVAL_K)
            contexts = [note_text(n) for n in notes]
            retrieved_slugs = [_TITLE_TO_SLUG.get(n.title, n.title) for n in notes]

            answer = await backend.generate_answer(sample.question, contexts)
            faith, precision, relevancy = await _score_sample(backend, sample, answer, contexts)

            answerable = bool(sample.relevant_slugs)
            recall: float | None = None
            if answerable:
                hit = sum(1 for s in sample.relevant_slugs if s in retrieved_slugs)
                recall = hit / len(sample.relevant_slugs)

            results.append(
                SampleResult(
                    question=sample.question,
                    ground_truth=sample.ground_truth,
                    answer=answer,
                    relevant_slugs=sample.relevant_slugs,
                    retrieved_slugs=retrieved_slugs,
                    answerable=answerable,
                    faithfulness=faith,
                    context_precision=precision,
                    answer_relevancy=relevancy,
                    retrieval_recall=recall,
                )
            )

    aggregates: dict[str, float | None] = {
        "faithfulness": _mean([r.faithfulness for r in results]),
        "context_precision": _mean([r.context_precision for r in results]),
        "answer_relevancy": _mean([r.answer_relevancy for r in results]),
        "retrieval_recall": _mean([r.retrieval_recall for r in results]),
    }

    return EvalReport(
        backend=backend.name,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        n_samples=len(results),
        aggregates=aggregates,
        samples=results,
    )
