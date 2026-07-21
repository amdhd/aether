"""RAGAS-equivalent metric implementations.

These re-implement the three canonical RAGAS metrics against Aether's own
DeepSeek judge and OpenAI embeddings (see ``backend.py``) rather than pulling in
the RAGAS package and its LangChain/dataset dependency tree. The formulas follow
the published RAGAS definitions so the numbers are comparable and defensible:

* **Faithfulness** — of the atomic claims in the answer, the fraction that are
  supported by the retrieved context. Measures hallucination: an answer that
  asserts things the context doesn't support scores low. (RAGAS: `faithfulness`.)

* **Context precision** — average precision over the ranked retrieved contexts,
  where a context is "relevant" if the judge finds it useful for reaching the
  reference answer. Rewards putting the useful contexts *first*. (RAGAS:
  `LLMContextPrecisionWithReference`.)

* **Answer relevancy** — mean cosine similarity between the original question
  and questions reverse-generated from the answer. Penalises answers that are
  incomplete or padded with irrelevant material. Noncommittal answers score 0.
  (RAGAS: `answer_relevancy`.)

Each returns a score in ``[0, 1]`` (answer relevancy is a cosine and can dip
slightly negative; it is clamped). ``None`` means "undefined for this sample"
(e.g. faithfulness of an answer with no extractable claims) and is excluded from
aggregate means rather than counted as zero.
"""

from __future__ import annotations

import asyncio
import math

from app.eval.backend import EvalBackend


async def faithfulness(backend: EvalBackend, answer: str, contexts: list[str]) -> float | None:
    claims = await backend.extract_claims(answer)
    if not claims:
        # No verifiable claims (e.g. an empty or purely conversational answer):
        # faithfulness is undefined rather than 0 or 1.
        return None
    context = "\n\n".join(contexts)
    verdicts = await asyncio.gather(
        *(backend.is_supported(context, claim) for claim in claims)
    )
    return sum(1 for v in verdicts if v) / len(claims)


async def context_precision(
    backend: EvalBackend, question: str, ground_truth: str, contexts: list[str]
) -> float | None:
    if not contexts:
        # Nothing was retrieved — precision is undefined (the retrieval-hit-rate
        # stat in the report is where an empty retrieval shows up).
        return None
    verdicts = await asyncio.gather(
        *(backend.is_relevant(question, ground_truth, ctx) for ctx in contexts)
    )
    relevances = [1 if v else 0 for v in verdicts]
    total_relevant = sum(relevances)
    if total_relevant == 0:
        # Retrieved contexts, but none useful: worst case, precision 0.
        return 0.0
    # Average precision: sum of Precision@k at each rank that holds a relevant
    # item, divided by the number of relevant items. Higher when relevant
    # contexts are ranked earlier.
    hits = 0
    precision_sum = 0.0
    for k, rel in enumerate(relevances, start=1):
        if rel:
            hits += 1
            precision_sum += hits / k
    return precision_sum / total_relevant


async def answer_relevancy(
    backend: EvalBackend, question: str, answer: str, n_questions: int = 3
) -> float | None:
    if await backend.is_noncommittal(answer):
        # An answer that declines ("the notes don't say") is maximally faithful
        # but not *relevant* to the question — RAGAS scores these 0.
        return 0.0
    generated = await backend.generate_questions(answer, n_questions)
    if not generated:
        return None
    q_vec = await backend.embed(question)
    if q_vec is None:
        # Embeddings unavailable (no OpenAI key): relevancy can't be computed.
        return None
    gen_vecs = await asyncio.gather(*(backend.embed(g) for g in generated))
    sims = [_cosine(q_vec, gv) for gv in gen_vecs if gv is not None]
    if not sims:
        return None
    score = sum(sims) / len(sims)
    return max(0.0, min(1.0, score))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
