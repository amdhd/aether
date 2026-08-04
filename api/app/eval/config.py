"""The knobs one eval run was configured with, and how they get recorded.

An eval that measures a single hard-coded point can tell you *what* the numbers
are but not *why*, and can't answer the question you actually run an eval to
answer: is k=8 better than k=4? So everything that moves a score is a field here,
every run stamps the resolved config into its report, and two reports are
therefore directly comparable — diff the ``config`` blocks and you know what
changed between them.

What's deliberately *not* here:

* **Chunk size.** Aether doesn't chunk. A note is embedded whole
  (``embeddings.embed_note``) and retrieved whole, so there is no chunking knob
  to sweep. If chunking is ever introduced, it belongs in this dataclass.
* **Model names and embedding dimensions.** Those live on the backend (see
  ``backend.py``), because the offline backend has no models and shouldn't be
  made to pretend it does — it would otherwise report the production embedding
  width for a 256-dim hashed bag of words. The harness reads them off the
  backend and folds them into the recorded config.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.eval.pipeline import RETRIEVAL_K

# Concurrent in-flight LLM requests. Bounded rather than unbounded because the
# fan-out is large — one generation plus roughly a dozen judge calls per sample,
# times the whole golden set — and providers rate-limit per minute. Eight is a
# conservative default that keeps a 50-sample run in the low minutes without
# tripping DeepSeek's limits; raise it if your account's limits allow.
DEFAULT_CONCURRENCY = 8

# Questions reverse-generated per answer for the answer-relevancy metric. RAGAS
# uses 3; more questions means a steadier mean cosine at linearly more cost.
DEFAULT_RELEVANCY_QUESTIONS = 3


@dataclass(frozen=True)
class EvalConfig:
    """Resolved parameters for one eval run."""

    retrieval_k: int = RETRIEVAL_K
    relevancy_questions: int = DEFAULT_RELEVANCY_QUESTIONS
    concurrency: int = DEFAULT_CONCURRENCY

    def __post_init__(self) -> None:
        if self.retrieval_k < 1:
            raise ValueError("retrieval_k must be >= 1")
        if self.relevancy_questions < 1:
            raise ValueError("relevancy_questions must be >= 1")
        if self.concurrency < 1:
            raise ValueError("concurrency must be >= 1")

    def to_dict(self) -> dict[str, object]:
        """The config as it goes into the report.

        Includes the pricing rates the cost estimate was computed with, since a
        cost figure is meaningless without them — a later reader comparing two
        reports needs to know whether a cost delta came from the config or from
        someone re-pointing the rates at a new price list.
        """
        return {
            "retrieval_k": self.retrieval_k,
            "relevancy_questions": self.relevancy_questions,
            "concurrency": self.concurrency,
            "note_search_max_distance": settings.NOTE_SEARCH_MAX_DISTANCE,
            "pricing_usd_per_1m": {
                "input": settings.LLM_INPUT_COST_PER_1M_TOKENS,
                "output": settings.LLM_OUTPUT_COST_PER_1M_TOKENS,
                "embedding": settings.EMBEDDING_COST_PER_1M_TOKENS,
            },
        }
