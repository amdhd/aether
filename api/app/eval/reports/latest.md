# RAG eval report

- **Backend:** `offline`
- **Samples:** 10
- **Generated:** 2026-08-04T04:51:07+00:00

## Configuration

- `retrieval_k=5`
- `relevancy_questions=3`
- `concurrency=8`
- `generation_model=offline-extractive`
- `judge_model=offline-token-overlap`
- `embedding_model=offline-hashed-bow (dim=256)`
- `note_search_max_distance=0.6`
- `pricing_usd_per_1m={'input': 0.27, 'output': 1.1, 'embedding': 0.02}`

## Aggregate scores

| Metric | Score |
| --- | --- |
| Faithfulness | 1.000 |
| Context precision | 0.717 |
| Answer relevancy | 0.356 |
| Retrieval recall | 1.000 |

## Cost & latency

| Measure | Value |
| --- | --- |
| Wall time (whole run) | 0.0s |
| Latency p50 / sample | 0.00s |
| Latency p95 / sample | 0.00s |
| LLM calls | 0 |
| Prompt / completion tokens | 0 / 0 |
| Embedding tokens | 0 |
| Estimated cost | $0.0000 |
| Estimated cost / sample | $0.000000 |

> ⚠️ Offline heuristic backend (no API keys). Scores are indicative only, and the token/cost figures are zero because nothing was billed.

## Per-sample

| Question | Answerable | Faith. | Ctx prec. | Ans. rel. | Recall | Tokens | Cost | Latency |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| When should I book the Ghibli Museum tickets for my Tokyo... | yes | 1.000 | 0.500 | 0.448 | 1.000 | 0 | $0.000000 | 0.00s |
| How many eggs do I use for carbonara for two people? | yes | 1.000 | 0.333 | 0.528 | 1.000 | 0 | $0.000000 | 0.00s |
| Should I put cream in carbonara? | yes | 1.000 | 1.000 | 0.358 | 1.000 | 0 | $0.000000 | 0.00s |
| What temperature and method do I bake the sourdough at? | yes | 1.000 | 1.000 | 0.122 | 1.000 | 0 | $0.000000 | 0.00s |
| What's the first thing to check when the on-call pager go... | yes | 1.000 | 0.333 | 0.276 | 1.000 | 0 | $0.000000 | 0.00s |
| When is my next car service due and what needs doing? | yes | 1.000 | 0.500 | 0.349 | 1.000 | 0 | $0.000000 | 0.00s |
| When does my ISA allowance reset? | yes | 1.000 | 1.000 | 0.451 | 1.000 | 0 | $0.000000 | 0.00s |
| What's my peak weekly mileage in the marathon plan? | yes | 1.000 | 0.500 | 0.349 | 1.000 | 0 | $0.000000 | 0.00s |
| What flight time is my Tokyo departure on 14 March? | no | 1.000 | 1.000 | 0.367 |   n/a | 0 | $0.000000 | 0.00s |
| What's the Wi-Fi password for the guest network? | no | 1.000 | 1.000 | 0.311 |   n/a | 0 | $0.000000 | 0.00s |
