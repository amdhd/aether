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
python -m app.eval.run                  # auto: LLM backend if keys are set, else offline
python -m app.eval.run --offline        # force the keyless heuristic backend
python -m app.eval.run --offline --check  # CI gate: exit non-zero below thresholds
```

Reports are written to `app/eval/reports/`: a timestamped JSON (full per-sample
detail) and `latest.md` (a committed human-readable summary).

### Comparing configurations

An eval that measures one hard-coded point tells you what the numbers are but
not what to change. Every knob that moves a score is a flag, and every run
stamps its resolved configuration into the report — so a sweep is two runs and a
diff:

```bash
python -m app.eval.run --k 4
python -m app.eval.run --k 8
```

Report filenames carry the backend and `k` (`eval-offline-k8-<stamp>.json`), so
successive runs leave a legible trail instead of overwriting each other.

| Flag | Default | What it changes |
| --- | --- | --- |
| `--k` | 5 | Top-K notes retrieved and handed to the generator. |
| `--relevancy-questions` | 3 | Questions reverse-generated per answer for answer relevancy. |
| `--concurrency` | 8 | Max LLM calls in flight at once. |
| `--gen-model` | `DEEPSEEK_MODEL` | Model that writes the answers. |
| `--judge-model` | `DEEPSEEK_MODEL` | Model that scores them. Separately settable, so "did the score move because the generator changed or the judge did?" is answerable. |
| `--embedding-model` | `EMBEDDING_MODEL` | Embeddings for retrieval *and* relevancy — both, so the two can't silently diverge. |

There is deliberately **no chunk-size knob**: Aether doesn't chunk. A note is
embedded whole by `embeddings.embed_note` and retrieved whole. If chunking is
ever introduced it belongs in `EvalConfig` alongside the rest.

### Cost and latency

Scores alone can't tell you whether a config is worth it — a retrieval change
that buys +0.02 faithfulness for 3x the tokens is a bad trade. So every sample
row also carries its own token counts, estimated USD, and wall time split by
phase (retrieval / generation / judging), and the report totals them. The
summary block looks like this (shape only — the figures below are illustrative,
not measured):

```
wall time               41.7s      <- the whole run
latency p50 / sample     4.62s     <- differs from wall time by ~the concurrency
latency p95 / sample     7.10s
LLM calls                  412
total tokens            186_240
est. cost              $0.0631
est. cost / sample     $0.006310
```

Token counts come off the `usage` field of responses the harness is already
making (see [`app/core/usage.py`](../core/usage.py), a task-local meter that
attributes usage to the sample that caused it even while samples run
concurrently). Costs are *estimates* priced from `LLM_INPUT_COST_PER_1M_TOKENS`,
`LLM_OUTPUT_COST_PER_1M_TOKENS`, and `EMBEDDING_COST_PER_1M_TOKENS` — the same
rates the production cost metric and monthly spend cap use. The report records
those rates, so a cost delta between two runs can be attributed to the config
rather than to someone re-pointing the price list.

On the offline backend all of these read zero, because nothing was billed.

### Concurrency

Retrieval runs sequentially — it shares one `AsyncSession`, which isn't safe for
concurrent use, and the default in-memory SQLite engine is a single connection.
Generation and judging then run for all samples at once, which is where the
round trips actually are: one generation plus roughly a dozen judge calls per
sample, so a 50-sample run is 500+ requests.

The bound is applied per *call*, not per sample (`ThrottledBackend`). Bounding
per sample would understate it, because scoring one sample fans out again
internally — one entailment call per claim, one relevance call per retrieved
context — so "8 samples at a time" can mean fifty concurrent requests. Holding
the semaphore around each individual call is what actually caps in-flight
requests at `--concurrency`, which is the number that has to stay under the
provider's rate limit.

### CI gate

The **RAG eval gate** job in [`.github/workflows/ci.yml`](../../../.github/workflows/ci.yml)
runs `--offline --check` on every push/PR. `--check` compares the aggregate
scores against `GATE_THRESHOLDS` in [`run.py`](run.py) and exits non-zero if any
metric falls below its floor — so a regression in retrieval or the metric
pipeline (e.g. [FM-1](FAILURE_MODES.md)) fails the build instead of merging. The
floors are tuned to the deterministic offline backend and set conservatively
below current scores, so the gate catches real drops without flaking. Raise them
(or add an LLM-backend profile) once real numbers are wired into CI.

Because the floors are empirical, `--check` prints a warning when it is run at a
configuration they weren't tuned for (a different backend, `k`, or relevancy
question count). Comparing them against another config is a category error in
either direction — it can fail a healthy change or pass a broken one — so the
run says so rather than reporting PASS/FAIL as if the number meant the same
thing.

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
| `config.py` | `EvalConfig` — the knobs a run is parameterised by, and what gets stamped into the report. |
| `pipeline.py` | Seeds the corpus and retrieves via the production `search_notes`. |
| `backend.py` | `LLMBackend` (DeepSeek + OpenAI), `OfflineBackend` (keyless heuristics), and `ThrottledBackend` (the in-flight bound). |
| `metrics.py` | The three RAGAS-equivalent metric implementations. |
| `harness.py` | Orchestrates a run and aggregates the report. |
| `run.py` | CLI entrypoint; writes JSON + Markdown reports. |
| `FAILURE_MODES.md` | Log of failure modes this harness surfaced, with status. |

Tests live in `api/tests/test_eval_metrics.py` (formula math, no LLM) and
`api/tests/test_eval_harness.py` (end-to-end smoke on the offline backend).
