"""Prometheus metrics for the controller itself (ARCHITECTURE.md §16).

These counters/gauges/histograms are defined once and incremented from the control
loop. Exposition at ``/metrics`` is wired up with the HTTP API (a later step); this
module owns the definitions so they can be incremented now.

Registered against a private :class:`CollectorRegistry` rather than the global
default so importing this module never collides with another process-global registry
and tests can read values deterministically.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()

DECISIONS_TOTAL = Counter(
    "datatool_decisions_total",
    "Decisions made by the decision engine.",
    ["kind", "surface"],
    registry=REGISTRY,
)

ACTIONS_TOTAL = Counter(
    "datatool_actions_total",
    "Actions taken by the orchestrator on the execution plane.",
    ["kind", "adapter", "succeeded"],
    registry=REGISTRY,
)

GUARDRAIL_EVALUATIONS_TOTAL = Counter(
    "datatool_guardrail_evaluations_total",
    "Guardrail evaluations performed.",
    ["guardrail", "breached", "severity"],
    registry=REGISTRY,
)

EXPERIMENTS_ACTIVE = Gauge(
    "datatool_experiments_active",
    "Experiments currently in each lifecycle state.",
    ["state"],
    registry=REGISTRY,
)

ADAPTER_CALL_DURATION_SECONDS = Histogram(
    "datatool_adapter_call_duration_seconds",
    "Duration of adapter calls.",
    ["adapter", "method"],
    registry=REGISTRY,
)

LOOP_DURATION_SECONDS = Histogram(
    "datatool_loop_duration_seconds",
    "Duration of a full control-loop tick.",
    registry=REGISTRY,
)
