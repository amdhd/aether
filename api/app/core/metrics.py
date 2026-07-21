"""Per-turn LLM metrics via CloudWatch Embedded Metric Format (EMF).

The agent loop already logs a human-readable ``llm.turn`` line and persists
token counts to ``usage_logs``. Neither of those becomes a *queryable* metric:
you can't alarm on cost or chart p95 latency from a grep line. This module emits
the same per-turn facts as an EMF document — a single JSON line to stdout that
the CloudWatch ``awslogs`` driver automatically extracts into custom metrics
(tokens, estimated cost, latency, tool calls) under ``METRICS_NAMESPACE``, with
**no extra infrastructure** (no PutMetricData calls, no agent, no IAM).

See the EMF spec: https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format_Specification.html

Design notes:

* The EMF line must be *pure JSON* — CloudWatch won't parse it if the app
  logger's ``asctime level name`` prefix is prepended. So this uses a dedicated
  logger with a message-only formatter and its own stdout handler.
* Emission is gated by ``EMF_METRICS_ENABLED`` so local dev logs stay
  human-readable; it's turned on in deployed environments.
* ``build_llm_turn_document`` and ``estimate_cost_usd`` are pure functions so the
  document shape and the cost math are unit-testable without capturing stdout.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

from app.core.config import settings

_EMF_LOGGER_NAME = "app.metrics.emf"
_emf_configured = False


def _emf_logger() -> logging.Logger:
    """A logger that writes the raw message and nothing else, to stdout.

    Kept separate from the app logger so the EMF line carries no human-readable
    prefix that would stop CloudWatch from parsing it as a metric document.
    """
    global _emf_configured
    logger = logging.getLogger(_EMF_LOGGER_NAME)
    if not _emf_configured:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False  # don't double-emit through the app/root logger
        _emf_configured = True
    return logger


def estimate_cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    """Approximate the USD cost of a turn from configured per-1M-token rates.

    An estimate for observability only — real billing comes from the provider.
    """
    input_cost = (prompt_tokens / 1_000_000) * settings.LLM_INPUT_COST_PER_1M_TOKENS
    output_cost = (completion_tokens / 1_000_000) * settings.LLM_OUTPUT_COST_PER_1M_TOKENS
    return round(input_cost + output_cost, 8)


def build_llm_turn_document(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    tool_calls: int,
    latency_ms: int,
    timestamp_ms: int | None = None,
) -> dict[str, Any]:
    """Build the EMF document for one completed LLM turn.

    Metrics are dimensioned by ``Environment`` and ``Model`` so cost and latency
    can be sliced per model and per deployment stage.
    """
    total_tokens = prompt_tokens + completion_tokens
    return {
        "_aws": {
            "Timestamp": timestamp_ms if timestamp_ms is not None else int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": settings.METRICS_NAMESPACE,
                    "Dimensions": [["Environment", "Model"]],
                    "Metrics": [
                        {"Name": "PromptTokens", "Unit": "Count"},
                        {"Name": "CompletionTokens", "Unit": "Count"},
                        {"Name": "TotalTokens", "Unit": "Count"},
                        {"Name": "EstimatedCostUsd", "Unit": "None"},
                        {"Name": "TurnLatencyMs", "Unit": "Milliseconds"},
                        {"Name": "ToolCalls", "Unit": "Count"},
                        {"Name": "Turns", "Unit": "Count"},
                    ],
                }
            ],
        },
        "Environment": settings.ENVIRONMENT,
        "Model": model,
        "PromptTokens": prompt_tokens,
        "CompletionTokens": completion_tokens,
        "TotalTokens": total_tokens,
        "EstimatedCostUsd": estimate_cost_usd(prompt_tokens, completion_tokens),
        "TurnLatencyMs": latency_ms,
        "ToolCalls": tool_calls,
        "Turns": 1,
    }


def emit_llm_turn(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    tool_calls: int,
    latency_ms: int,
) -> None:
    """Emit a per-turn EMF metric line, if EMF metrics are enabled.

    A no-op when ``EMF_METRICS_ENABLED`` is false (local dev), so callers can
    invoke it unconditionally without leaking JSON into human-readable logs.
    """
    if not settings.EMF_METRICS_ENABLED:
        return
    document = build_llm_turn_document(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        tool_calls=tool_calls,
        latency_ms=latency_ms,
    )
    _emf_logger().info(json.dumps(document))
