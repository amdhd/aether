"""CLI entrypoint for the RAG eval harness.

    python -m app.eval.run                 # auto: LLM backend if keys, else offline
    python -m app.eval.run --offline       # force the keyless heuristic backend
    python -m app.eval.run --json-only     # write reports, skip the console table
    python -m app.eval.run --offline --check   # CI gate: exit non-zero below floors

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

from app.eval.backend import OfflineBackend, get_backend
from app.eval.harness import EvalReport, run_eval

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


def _print_summary(report: EvalReport) -> None:
    print(f"\nRAG eval — backend={report.backend}  samples={report.n_samples}")
    print(f"generated_at={report.generated_at}\n")
    print(f"{'metric':<20} {'score':>7}")
    print("-" * 28)
    for key, label in _METRIC_LABELS.items():
        print(f"{label:<20} {_fmt(report.aggregates.get(key)):>7}")
    print()
    if report.backend == OfflineBackend.name:
        print(
            "NOTE: offline heuristic backend (no DeepSeek/OpenAI key). Scores are "
            "indicative only — set the keys for authoritative numbers.\n"
        )


def _render_markdown(report: EvalReport) -> str:
    lines = [
        "# RAG eval report",
        "",
        f"- **Backend:** `{report.backend}`",
        f"- **Samples:** {report.n_samples}",
        f"- **Generated:** {report.generated_at}",
        "",
        "## Aggregate scores",
        "",
        "| Metric | Score |",
        "| --- | --- |",
    ]
    for key, label in _METRIC_LABELS.items():
        lines.append(f"| {label} | {_fmt(report.aggregates.get(key))} |")
    if report.backend == OfflineBackend.name:
        lines += [
            "",
            "> ⚠️ Offline heuristic backend (no API keys). Indicative only.",
        ]
    lines += ["", "## Per-sample", "", "| Question | Answerable | Faith. | Ctx prec. | Ans. rel. | Recall |", "| --- | --- | --- | --- | --- | --- |"]
    for s in report.samples:
        q = s.question if len(s.question) <= 60 else s.question[:57] + "..."
        lines.append(
            f"| {q} | {'yes' if s.answerable else 'no'} | {_fmt(s.faithfulness)} "
            f"| {_fmt(s.context_precision)} | {_fmt(s.answer_relevancy)} | {_fmt(s.retrieval_recall)} |"
        )
    lines.append("")
    return "\n".join(lines)


def _write_reports(report: EvalReport) -> tuple[Path, Path]:
    REPORTS_DIR.mkdir(exist_ok=True)
    stamp = report.generated_at.replace(":", "").replace("-", "")
    json_path = REPORTS_DIR / f"eval-{report.backend}-{stamp}.json"
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


async def _main_async(args: argparse.Namespace) -> int:
    url = _eval_database_url()
    engine = _make_engine(url)
    await _create_schema(engine)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    backend = OfflineBackend() if args.offline else get_backend()
    try:
        report = await run_eval(session_factory, backend)
    finally:
        await engine.dispose()

    json_path, md_path = _write_reports(report)
    if not args.json_only:
        _print_summary(report)
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")

    if args.check:
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
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
