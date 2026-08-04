"""CLI entrypoint for the RAG eval harness.

    python -m app.eval.run                 # auto: LLM backend if keys, else offline
    python -m app.eval.run --offline       # force the keyless heuristic backend
    python -m app.eval.run --json-only     # write reports, skip the console table
    python -m app.eval.run --offline --check   # CI gate: exit non-zero below floors

Sweeping a knob is the point of the flags — run the same suite twice and diff the
``config`` blocks of the two reports against their ``aggregates`` and ``totals``::

    python -m app.eval.run --k 4
    python -m app.eval.run --k 8

Report filenames carry the backend and k, so successive runs don't overwrite
each other's JSON.

Database selection (mirrors the test suite):

* Default — an ephemeral in-memory SQLite DB. Portable and keyless; retrieval
  uses the keyword fallback.
* ``EVAL_DATABASE_URL`` (or ``DATABASE_URL``) pointing at Postgres + pgvector,
  with ``OPENAI_API_KEY`` set — exercises true semantic retrieval. This is the
  configuration to quote numbers from.

Reports are written to ``app/eval/reports/`` as timestamped JSON plus a
``latest.md`` summary that is safe to commit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base

# Import every model so Base.metadata is fully populated before create_all.
from app.models import note as _note  # noqa: F401
from app.models import user as _user  # noqa: F401

from app.core.config import settings
from app.eval.backend import OfflineBackend, get_backend
from app.eval.config import DEFAULT_CONCURRENCY, DEFAULT_RELEVANCY_QUESTIONS, EvalConfig
from app.eval.harness import EvalReport, run_eval
from app.eval.pipeline import RETRIEVAL_K

REPORTS_DIR = Path(__file__).parent / "reports"

_METRIC_LABELS = {
    "faithfulness": "Faithfulness",
    "context_precision": "Context precision",
    "answer_relevancy": "Answer relevancy",
    "retrieval_recall": "Retrieval recall",
}

# Minimum aggregate scores the CI gate (`--check`) enforces. Tuned to the
# deterministic *offline* backend, which produces stable scores over the fixed
# golden set, and set conservatively below current values so the gate catches a
# real regression (e.g. retrieval breaking, as FM-1 did) without flaking. Raise
# these — or add an LLM-backend profile — once real numbers are wired in CI.
GATE_THRESHOLDS: dict[str, float] = {
    "faithfulness": 0.95,
    "context_precision": 0.65,
    "answer_relevancy": 0.30,
    "retrieval_recall": 0.99,
}


def _eval_database_url() -> str:
    return os.getenv("EVAL_DATABASE_URL") or os.getenv("DATABASE_URL") or "sqlite+aiosqlite://"


def _make_engine(url: str):
    if url.startswith("sqlite"):
        return create_async_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    return create_async_engine(url)


async def _create_schema(engine) -> None:
    is_sqlite = engine.url.get_backend_name() == "sqlite"
    async with engine.begin() as conn:
        if not is_sqlite:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)


def _fmt(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "  n/a"


def _config_lines(report: EvalReport) -> list[str]:
    """The knobs this run used, in a fixed order, as ``key=value`` strings.

    Printed on every run and written into every report: a score is only
    comparable against another score taken at a known configuration.
    """
    config = report.config
    pricing = config.get("pricing_usd_per_1m", {})
    return [
        f"retrieval_k={config.get('retrieval_k')}",
        f"relevancy_questions={config.get('relevancy_questions')}",
        f"concurrency={config.get('concurrency')}",
        f"generation_model={config.get('generation_model')}",
        f"judge_model={config.get('judge_model')}",
        f"embedding_model={config.get('embedding_model')}"
        f" (dim={config.get('embedding_dimensions')})",
        f"note_search_max_distance={config.get('note_search_max_distance')}",
        f"pricing_usd_per_1m={pricing}",
    ]


def _print_summary(report: EvalReport) -> None:
    print(f"\nRAG eval — backend={report.backend}  samples={report.n_samples}")
    print(f"generated_at={report.generated_at}\n")
    print("config")
    for line in _config_lines(report):
        print(f"  {line}")
    print()
    print(f"{'metric':<20} {'score':>7}")
    print("-" * 28)
    for key, label in _METRIC_LABELS.items():
        print(f"{label:<20} {_fmt(report.aggregates.get(key)):>7}")

    totals = report.totals
    usage = totals.get("usage", {})
    print()
    print(f"{'cost / latency':<20}")
    print("-" * 28)
    print(f"{'wall time':<20} {totals.get('wall_seconds', 0):>7.1f}s")
    print(f"{'latency p50 / sample':<20} {(totals.get('sample_latency_ms_p50') or 0) / 1000:>7.2f}s")
    print(f"{'latency p95 / sample':<20} {(totals.get('sample_latency_ms_p95') or 0) / 1000:>7.2f}s")
    print(f"{'LLM calls':<20} {usage.get('llm_calls', 0):>7}")
    print(f"{'total tokens':<20} {usage.get('total_tokens', 0):>7}")
    print(f"{'est. cost':<20} ${totals.get('estimated_cost_usd', 0):>.4f}")
    print(f"{'est. cost / sample':<20} ${totals.get('estimated_cost_usd_per_sample', 0):>.6f}")
    print()
    if report.backend == OfflineBackend.name:
        print(
            "NOTE: offline heuristic backend (no DeepSeek/OpenAI key). Scores are "
            "indicative only — set the keys for authoritative numbers. Token and "
            "cost figures are zero because nothing was billed.\n"
        )


def _render_markdown(report: EvalReport) -> str:
    lines = [
        "# RAG eval report",
        "",
        f"- **Backend:** `{report.backend}`",
        f"- **Samples:** {report.n_samples}",
        f"- **Generated:** {report.generated_at}",
        "",
        "## Configuration",
        "",
    ]
    lines += [f"- `{line}`" for line in _config_lines(report)]
    lines += [
        "",
        "## Aggregate scores",
        "",
        "| Metric | Score |",
        "| --- | --- |",
    ]
    for key, label in _METRIC_LABELS.items():
        lines.append(f"| {label} | {_fmt(report.aggregates.get(key))} |")

    totals = report.totals
    usage = totals.get("usage", {})
    lines += [
        "",
        "## Cost & latency",
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| Wall time (whole run) | {totals.get('wall_seconds', 0):.1f}s |",
        f"| Latency p50 / sample | {(totals.get('sample_latency_ms_p50') or 0) / 1000:.2f}s |",
        f"| Latency p95 / sample | {(totals.get('sample_latency_ms_p95') or 0) / 1000:.2f}s |",
        f"| LLM calls | {usage.get('llm_calls', 0)} |",
        f"| Prompt / completion tokens | {usage.get('prompt_tokens', 0)} / {usage.get('completion_tokens', 0)} |",
        f"| Embedding tokens | {usage.get('embedding_tokens', 0)} |",
        f"| Estimated cost | ${totals.get('estimated_cost_usd', 0):.4f} |",
        f"| Estimated cost / sample | ${totals.get('estimated_cost_usd_per_sample', 0):.6f} |",
    ]
    if report.backend == OfflineBackend.name:
        lines += [
            "",
            "> ⚠️ Offline heuristic backend (no API keys). Scores are indicative only, "
            "and the token/cost figures are zero because nothing was billed.",
        ]
    lines += [
        "",
        "## Per-sample",
        "",
        "| Question | Answerable | Faith. | Ctx prec. | Ans. rel. | Recall | Tokens | Cost | Latency |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in report.samples:
        q = s.question if len(s.question) <= 60 else s.question[:57] + "..."
        lines.append(
            f"| {q} | {'yes' if s.answerable else 'no'} | {_fmt(s.faithfulness)} "
            f"| {_fmt(s.context_precision)} | {_fmt(s.answer_relevancy)} | {_fmt(s.retrieval_recall)} "
            f"| {s.usage.total_tokens} | ${s.estimated_cost_usd:.6f} "
            f"| {s.timings_ms.total / 1000:.2f}s |"
        )
    lines.append("")
    return "\n".join(lines)


def _write_reports(report: EvalReport) -> tuple[Path, Path]:
    REPORTS_DIR.mkdir(exist_ok=True)
    stamp = report.generated_at.replace(":", "").replace("-", "")
    # k is in the filename so a sweep leaves a legible trail on disk rather than
    # a pile of timestamps you have to open to tell apart.
    json_path = REPORTS_DIR / f"eval-{report.backend}-k{report.config.get('retrieval_k')}-{stamp}.json"
    json_path.write_text(json.dumps(report.to_dict(), indent=2))
    md_path = REPORTS_DIR / "latest.md"
    md_path.write_text(_render_markdown(report))
    return json_path, md_path


def _check_gate(report: EvalReport) -> list[str]:
    """Return a list of human-readable failures where an aggregate is below its
    threshold (or undefined). Empty list = the gate passes."""
    failures: list[str] = []
    for key, threshold in GATE_THRESHOLDS.items():
        value = report.aggregates.get(key)
        label = _METRIC_LABELS.get(key, key)
        if value is None:
            failures.append(f"{label}: undefined (expected ≥ {threshold:.2f})")
        elif value < threshold:
            failures.append(f"{label}: {value:.3f} < {threshold:.2f}")
    return failures


def _gate_config_warnings(report: EvalReport) -> list[str]:
    """Flag a gated run whose config isn't the one the floors were tuned to.

    The thresholds are empirical, not principled — they were read off the
    offline backend at the default k. Comparing them against a run at some other
    configuration is a category error in either direction: it can fail a healthy
    change or pass a broken one. So say so rather than silently reporting
    PASS/FAIL as if the number meant the same thing.
    """
    warnings: list[str] = []
    if report.backend != OfflineBackend.name:
        warnings.append(f"backend={report.backend} (floors are tuned to '{OfflineBackend.name}')")
    if report.config.get("retrieval_k") != RETRIEVAL_K:
        warnings.append(f"retrieval_k={report.config.get('retrieval_k')} (tuned at {RETRIEVAL_K})")
    if report.config.get("relevancy_questions") != DEFAULT_RELEVANCY_QUESTIONS:
        warnings.append(
            f"relevancy_questions={report.config.get('relevancy_questions')} "
            f"(tuned at {DEFAULT_RELEVANCY_QUESTIONS})"
        )
    return warnings


async def _main_async(args: argparse.Namespace) -> int:
    # Retrieval embeds the query through the shared embeddings service, which
    # reads this at call time, so setting it here keeps the retriever and the
    # backend's relevancy embeddings on the same model. (Equivalent to exporting
    # EMBEDDING_MODEL; the flag just makes a sweep a one-liner.)
    if args.embedding_model:
        settings.EMBEDDING_MODEL = args.embedding_model

    config = EvalConfig(
        retrieval_k=args.k,
        relevancy_questions=args.relevancy_questions,
        concurrency=args.concurrency,
    )

    url = _eval_database_url()
    engine = _make_engine(url)
    await _create_schema(engine)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    if args.offline and (args.gen_model or args.judge_model):
        # Silently ignoring these would leave a sweep looking like it varied the
        # models when it varied nothing at all.
        print("NOTE: --gen-model/--judge-model are ignored on the offline backend.")
    backend = (
        OfflineBackend()
        if args.offline
        else get_backend(generation_model=args.gen_model, judge_model=args.judge_model)
    )
    try:
        report = await run_eval(session_factory, backend, config)
    finally:
        await engine.dispose()

    json_path, md_path = _write_reports(report)
    if not args.json_only:
        _print_summary(report)
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")

    if args.check:
        for warning in _gate_config_warnings(report):
            print(f"\nEVAL GATE: WARNING — thresholds not tuned for this config: {warning}")
        failures = _check_gate(report)
        if failures:
            print("\nEVAL GATE: FAIL")
            for failure in failures:
                print(f"  ✗ {failure}")
            return 1
        print("\nEVAL GATE: PASS (all metrics at or above thresholds)")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RAG eval harness.")
    parser.add_argument("--offline", action="store_true", help="force the keyless heuristic backend")
    parser.add_argument("--json-only", action="store_true", help="skip the console summary table")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if any aggregate is below its GATE_THRESHOLDS floor (CI gate)",
    )
    knobs = parser.add_argument_group(
        "configuration",
        "Every knob here is recorded in the report, so two runs are comparable.",
    )
    knobs.add_argument(
        "--k",
        type=int,
        default=RETRIEVAL_K,
        metavar="N",
        help=f"top-K notes handed to the generator (default: {RETRIEVAL_K})",
    )
    knobs.add_argument(
        "--relevancy-questions",
        type=int,
        default=DEFAULT_RELEVANCY_QUESTIONS,
        metavar="N",
        help=f"questions reverse-generated per answer (default: {DEFAULT_RELEVANCY_QUESTIONS})",
    )
    knobs.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        metavar="N",
        help=f"max LLM calls in flight at once (default: {DEFAULT_CONCURRENCY})",
    )
    knobs.add_argument("--gen-model", metavar="MODEL", help="override the generation model")
    knobs.add_argument("--judge-model", metavar="MODEL", help="override the judge model")
    knobs.add_argument("--embedding-model", metavar="MODEL", help="override the embeddings model")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
