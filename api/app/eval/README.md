# RAG evaluation harness

A reproducible eval suite for Aether's note-search RAG pipeline. It measures the
three canonical [RAGAS](https://docs.ragas.io/) metrics — **faithfulness**,
**context precision**, and **answer relevancy** — against a curated golden
dataset, plus a **retrieval recall** sanity stat.

It re-implements the RAGAS metric definitions against Aether's own DeepSeek
judge and OpenAI embeddings rather than pulling in the RAGAS package and its
LangChain/dataset dependency tree. The formulas follow the published RAGAS
definitions, so the numbers are comparable and — more importantly for a review —
defensible line by line. (Swapping in the real `ragas` package later is
straightforward: it consumes the same `{question, answer, contexts,
ground_truth}` rows the harness already builds.)

## What it evaluates

The harness drives the **real** retriever — the same
`note_search.search_notes` the agent's `search_notes` tool calls in production —
so it measures what ships, not a mock:

```
golden question ──▶ search_notes (pgvector semantic / keyword fallback)
                        │
                        ▼
                 retrieved notes ──▶ DeepSeek generation ──▶ answer
                        │                                       │
                        └──────────── metrics (LLM judge) ◀─────┘
```

## Metrics

| Metric | Question it answers | Definition (per sample) |
| --- | --- | --- |
| **Faithfulness** | Is the answer grounded, or hallucinated? | Fraction of the answer's atomic claims that are entailed by the retrieved context. |
| **Context precision** | Did retrieval rank the *useful* notes first? | Rank-weighted average precision over the retrieved notes, where a note is "relevant" if the judge finds it useful for reaching the reference answer. |
| **Answer relevancy** | Does the answer actually address the question? | Mean cosine similarity between the question and questions reverse-generated from the answer; noncommittal answers score 0. |
| **Retrieval recall** | Did retrieval find the labelled notes at all? | Non-LLM sanity stat: fraction of a sample's labelled relevant notes that appear in the top-K. |

A metric is `None` ("n/a") when it is *undefined* for a sample — e.g.
faithfulness of an answer with no extractable claims, or context precision when
nothing was retrieved — and such samples are excluded from the aggregate mean
rather than counted as zero.

## Running it

```bash
cd api
python -m app.eval.run            # auto: LLM backend if keys are set, else offline
python -m app.eval.run --offline  # force the keyless heuristic backend
```

Reports are written to `app/eval/reports/`: a timestamped JSON (full per-sample
detail) and `latest.md` (a committed human-readable summary).

### Backends

| Backend | When | Generation & judging | Notes |
| --- | --- | --- | --- |
| `llm` | `DEEPSEEK_API_KEY` set | DeepSeek (generation + judge), OpenAI embeddings | **Authoritative.** Run this for real numbers. |
| `offline` | no key | Deterministic token-overlap heuristics | Keyless; for CI and plumbing. Scores are indicative only. |

### Database / retrieval fidelity

Retrieval quality depends on the store, exactly as in production:

- **SQLite / no OpenAI key** (default) — `search_notes` degrades to a keyword
  scan. The harness runs end to end; treat the numbers as a keyword-RAG
  baseline.
- **Postgres + pgvector + `OPENAI_API_KEY`** — notes are embedded and retrieval
  is true cosine-distance semantic search including the relevance floor. Point
  the harness at it with `EVAL_DATABASE_URL` (or `DATABASE_URL`). **This is the
  configuration to quote headline numbers from.**

```bash
EVAL_DATABASE_URL=postgresql://user:pass@localhost:5432/aether_eval \
OPENAI_API_KEY=sk-... DEEPSEEK_API_KEY=sk-... \
python -m app.eval.run
```

## Layout

| File | Role |
| --- | --- |
| `dataset.py` | The golden corpus (notes) and eval samples (Q + reference answer + labelled notes), including deliberately *unanswerable* samples. |
| `pipeline.py` | Seeds the corpus and retrieves via the production `search_notes`. |
| `backend.py` | `LLMBackend` (DeepSeek + OpenAI) and `OfflineBackend` (keyless heuristics). |
| `metrics.py` | The three RAGAS-equivalent metric implementations. |
| `harness.py` | Orchestrates a run and aggregates the report. |
| `run.py` | CLI entrypoint; writes JSON + Markdown reports. |
| `FAILURE_MODES.md` | Log of failure modes this harness surfaced, with status. |

Tests live in `api/tests/test_eval_metrics.py` (formula math, no LLM) and
`api/tests/test_eval_harness.py` (end-to-end smoke on the offline backend).
