"""Tests for the CI eval gate (`run.py --check`)."""

from app.eval import run
from app.eval.backend import OfflineBackend
from app.eval.harness import EvalReport, run_eval
from tests.conftest import TestingSessionLocal


def _report(aggregates: dict) -> EvalReport:
    return EvalReport(
        backend="offline",
        generated_at="2026-01-01T00:00:00+00:00",
        n_samples=1,
        aggregates=aggregates,
        samples=[],
    )


def test_gate_passes_when_all_metrics_meet_thresholds() -> None:
    # Exactly at the floor passes (>= comparison).
    report = _report(dict(run.GATE_THRESHOLDS))
    assert run._check_gate(report) == []


def test_gate_fails_and_names_metrics_below_threshold() -> None:
    aggregates = dict(run.GATE_THRESHOLDS)
    aggregates["retrieval_recall"] = 0.5  # a retrieval regression (cf. FM-1)
    failures = run._check_gate(report=_report(aggregates))
    assert len(failures) == 1
    assert "Retrieval recall" in failures[0]


def test_gate_fails_when_a_gated_metric_is_undefined() -> None:
    aggregates = dict(run.GATE_THRESHOLDS)
    aggregates["faithfulness"] = None  # couldn't be computed at all
    failures = run._check_gate(report=_report(aggregates))
    assert any("Faithfulness" in f and "undefined" in f for f in failures)


async def test_offline_backend_meets_the_committed_thresholds() -> None:
    # Guards against the gate being set above what the offline harness actually
    # produces (which would make CI fail on a clean tree). The deterministic
    # offline scores must clear every floor.
    report = await run_eval(TestingSessionLocal, OfflineBackend())
    assert run._check_gate(report) == []
