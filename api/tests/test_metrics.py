"""Tests for per-turn LLM metrics (cost model + EMF document + emission gate)."""

import json
import logging

import pytest

from app.core import metrics


def test_estimate_cost_uses_configured_rates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metrics.settings, "LLM_INPUT_COST_PER_1M_TOKENS", 1.0)
    monkeypatch.setattr(metrics.settings, "LLM_OUTPUT_COST_PER_1M_TOKENS", 2.0)
    # 1M input @ $1 + 0.5M output @ $2 = $1 + $1 = $2.
    assert metrics.estimate_cost_usd(1_000_000, 500_000) == pytest.approx(2.0)


def test_estimate_cost_zero_tokens_is_zero() -> None:
    assert metrics.estimate_cost_usd(0, 0) == 0.0


def test_build_document_shape_is_valid_emf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metrics.settings, "METRICS_NAMESPACE", "Aether/LLM")
    monkeypatch.setattr(metrics.settings, "ENVIRONMENT", "production")

    doc = metrics.build_llm_turn_document(
        model="deepseek-v4-flash",
        prompt_tokens=100,
        completion_tokens=40,
        tool_calls=2,
        latency_ms=1234,
        timestamp_ms=1_700_000_000_000,
    )

    # EMF envelope.
    meta = doc["_aws"]
    assert meta["Timestamp"] == 1_700_000_000_000
    directive = meta["CloudWatchMetrics"][0]
    assert directive["Namespace"] == "Aether/LLM"
    # Per-(Environment, Model) plus an Environment-only rollup for alarming.
    assert directive["Dimensions"] == [["Environment", "Model"], ["Environment"]]

    # Every declared metric and every referenced dimension must exist as a
    # top-level field, or CloudWatch silently drops the document.
    metric_names = {m["Name"] for m in directive["Metrics"]}
    for dimension in {d for dim_set in directive["Dimensions"] for d in dim_set}:
        assert dimension in doc
    for name in metric_names:
        assert name in doc

    # Values.
    assert doc["Environment"] == "production"
    assert doc["Model"] == "deepseek-v4-flash"
    assert doc["PromptTokens"] == 100
    assert doc["CompletionTokens"] == 40
    assert doc["TotalTokens"] == 140
    assert doc["TurnLatencyMs"] == 1234
    assert doc["ToolCalls"] == 2
    assert doc["Turns"] == 1


def test_emit_is_noop_when_disabled(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(metrics.settings, "EMF_METRICS_ENABLED", False)
    metrics.emit_llm_turn(
        model="m", prompt_tokens=1, completion_tokens=1, tool_calls=0, latency_ms=1
    )
    assert capsys.readouterr().out == ""


def test_emit_writes_pure_json_line_when_enabled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(metrics.settings, "EMF_METRICS_ENABLED", True)
    # Force the EMF handler to (re)bind to capsys's patched stdout, so the
    # emitted line is captured regardless of whether it was configured earlier.
    metrics._emf_configured = False
    logging.getLogger(metrics._EMF_LOGGER_NAME).handlers.clear()
    metrics.emit_llm_turn(
        model="deepseek-v4-flash",
        prompt_tokens=10,
        completion_tokens=5,
        tool_calls=1,
        latency_ms=42,
    )
    out = capsys.readouterr().out.strip()
    # Must be a single line of parseable JSON with no log prefix — otherwise the
    # CloudWatch EMF parser won't recognise it.
    assert "\n" not in out
    parsed = json.loads(out)
    assert parsed["TotalTokens"] == 15
    assert parsed["_aws"]["CloudWatchMetrics"][0]["Namespace"] == metrics.settings.METRICS_NAMESPACE
