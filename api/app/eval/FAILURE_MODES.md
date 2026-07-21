# RAG failure-mode log

A running log of failure modes in Aether's note-search RAG pipeline — how each
was found, what causes it, its status, and how the eval harness guards against
regression. This is the artifact the eval suite exists to produce: an eval that
never changes anything is just a dashboard.

Each entry: **symptom → root cause → status → regression guard.**

---

## FM-1 — Keyword fallback matched nothing for natural-language questions

- **Severity:** high (silent — retrieval returned empty, generation then
  answered from the model's parametric memory or declined).
- **Symptom:** On the first harness run (offline backend, SQLite), *every*
  metric was `0`/`n/a` and retrieval recall was `0.00`. No note was ever
  retrieved for a question phrased as a sentence.
- **Root cause:** The keyword fallback in `note_search._keyword_search` matched
  the **entire query string** as one `LIKE '%...%'` pattern. The literal
  sentence "how many eggs do i use for carbonara?" never appears verbatim in a
  note, so nothing matched. The fallback only ever worked for single-word
  queries.
- **Status:** **Fixed.** `_keyword_search` now tokenizes the query into content
  words (dropping stopwords) and matches notes containing *any* term. Retrieval
  recall on the golden set went from `0.00` to `1.00`.
- **Regression guard:** `tests/test_rag.py::test_keyword_search_matches_natural_language_question`
  and `::test_keyword_search_ignores_stopword_only_query`; the harness smoke
  test asserts retrieval recall stays at 1.0 for answerable samples.
- **Note:** This only affects the *keyless / SQLite* path. In production
  (Postgres + pgvector + embeddings) the semantic path handles NL queries; the
  fix hardens the degraded fallback so keyless dev/CI behaves sensibly too.

---

## FM-2 — Keyword fallback over-retrieves, dragging down context precision

- **Severity:** medium (precision, not recall — noisy context, not missing
  context).
- **Symptom:** With the FM-1 fix in place, context precision on the offline
  backend sits around **0.72**, not 1.0: answerable samples pull in unrelated
  notes that happen to share a common word (e.g. a note mentioning "two" or a
  date matching a travel question).
- **Root cause:** Term-OR keyword matching is inherently low-precision — it has
  no notion of relevance ranking, only presence. The relevance *floor* that
  protects the semantic path (`NOTE_SEARCH_MAX_DISTANCE`) has no equivalent in
  the keyword path.
- **Status:** **Accepted / by design.** The keyword path is a degraded
  fallback; the real fix is to run the semantic path (pgvector), which ranks by
  cosine distance and applies the floor. Documented here so the number isn't
  mistaken for a semantic-retrieval result.
- **Regression guard:** Tracked by the `context_precision` aggregate in every
  report; a drop on the *semantic* (Postgres) run would be the signal that
  matters.

---

## FM-3 — Hallucination risk on unanswerable questions

- **Severity:** high (this is the classic RAG hallucination failure — the one
  users notice and lose trust over).
- **Symptom:** Two golden samples are deliberately unanswerable from the corpus
  ("what flight *time* is my Tokyo departure?", "what's the Wi-Fi *password*?").
  A note *about* the topic is retrieved (the Tokyo trip note, the home-network
  note), but it does not contain the specific fact asked for. The risk is that
  generation fills the gap with a plausible invention.
- **Root cause:** Retrieval returns topically-adjacent context; the generator
  must recognise that the *specific* fact is absent and decline rather than
  interpolate.
- **Status:** **Measured, mitigated by prompt.** The generation prompt in
  `LLMBackend` instructs the model to answer *only* from the notes and to say so
  when the answer is absent. Faithfulness on these samples is the metric that
  catches a regression: a fabricated flight time is a claim unsupported by the
  context, so faithfulness drops.
- **Known limitation:** The **offline** backend cannot detect unanswerability —
  its extractive generator just returns the most word-overlapping sentences, so
  it reports faithfulness 1.0 on these samples (the text is copied from the
  context, hence trivially "supported"). Only the `llm` backend meaningfully
  evaluates FM-3. This is called out so an offline report's faithfulness on the
  unanswerable rows is not read as evidence of correct declining behaviour.
- **Regression guard:** The two `relevant_slugs: []` samples in `dataset.py`;
  faithfulness aggregate on the `llm` backend.

---

## FM-4 — Answer relevancy is low even when answers are correct (offline only)

- **Severity:** low (measurement artifact, not a pipeline defect).
- **Symptom:** Answer relevancy on the offline backend is ~0.35 even for
  obviously correct answers.
- **Root cause:** The offline backend's "embeddings" are a deterministic
  hashing bag-of-words, and its "reverse-generated questions" are just the
  answer's own sentences. Cosine similarity between a question and a
  declarative answer sentence is genuinely modest under bag-of-words. This
  measures lexical overlap, not semantics.
- **Status:** **Expected.** Answer relevancy is only meaningful on the `llm`
  backend with real OpenAI embeddings and LLM-generated questions. The offline
  number exists to prove the metric plumbing runs, nothing more.
- **Regression guard:** `tests/test_eval_metrics.py::test_answer_relevancy_is_mean_cosine`
  pins the math with controlled vectors, independent of the backend.

---

## How to add an entry

When a harness run (or a production incident) surfaces a new failure mode:

1. Add a row to the golden dataset in `dataset.py` that reproduces it — ideally
   one that scores badly *before* the fix.
2. Log it here: symptom, root cause, status, regression guard.
3. Fix it (or record why it's accepted), and add/point to the test that keeps it
   fixed.
