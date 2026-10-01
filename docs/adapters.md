# Writing an adapter

Adapters are how DataTool touches the outside world: feature-flag systems, metrics warehouses, variant generators, notification channels. They are the entire execution plane. The control plane — the contract, the statistics, the decision engine, the state machine — depends only on the adapter *protocols*, never on a concrete vendor. That separation is a deliberate design goal: any integrator should be able to delete every bundled adapter, register their own, and keep the brain unchanged (`ARCHITECTURE.md §11`, `§22`).

This doc explains the four protocols, the registry that wires them in, and gives a copy-paste skeleton for each.

## Protocols, not inheritance

Every adapter category is a [PEP 544](https://peps.python.org/pep-0544/) `Protocol` (structural typing), not a base class to subclass. There is no `AbstractFlagProvider` anywhere. A class is a `FlagProvider` if it has the right methods and an `adapter_id` attribute — nothing more. This is enforced by the immutable rules in [`CLAUDE.md`](../CLAUDE.md):

- **No business logic depends on a specific vendor.** `core/` and `control/` import the *protocol* and resolve concrete adapters by id at runtime. They never `import datatool.adapters.flag.postgres`.
- **Adapters are trivially testable.** Because the protocol is structural, a fake adapter in a test is just a small class with the right methods — no inheritance, no mocking framework.
- The protocols are `@runtime_checkable`, so `isinstance(x, FlagProvider)` works for defensive checks.

## The registry

Every concrete adapter has a stable, dotted **adapter id** (`flag.postgres`, `metrics.csv`, `metrics.posthog`, `notify.slack`, `notify.webhook`, `variant.static`, `variant.llm`) and registers itself under that id in [`datatool/adapters/base.py`](../datatool/adapters/base.py).

The registry stores a **factory** (a callable that builds a configured instance), not an instance, so construction — opening connections, reading env vars — happens lazily when the daemon wires things up, not at import time. In the bundled adapters the factory is simply the class itself; the wiring layer supplies the constructor arguments.

```python
from datatool.adapters import base as registry

registry.register(adapter_id, factory)        # raises if id is blank or already taken
registry.get_adapter_factory(adapter_id)       # raises if nothing is registered there
registry.is_registered(adapter_id)             # bool
registry.registered_adapter_ids()              # sorted list
registry.clear_registry()                      # test isolation only
```

`register()` refuses to overwrite an existing id — a silent overwrite could swap a vendor implementation out from under the controller, so it raises `AdapterError` instead. The id namespace is flat, but ids are dotted by category, so cross-category collisions cannot happen.

Each adapter module exposes a module-level `register()` that the edge (the CLI and daemon) calls once at startup. The control plane never imports the module; it only ever resolves by id.

## The four protocols

### `FlagProvider` — the flag plane (`ARCHITECTURE.md §11.1`)

The execution-plane handle the orchestrator uses to change what users see. Defined in [`datatool/adapters/flag/base.py`](../datatool/adapters/flag/base.py). Reference implementation: `flag/postgres.py`.

```python
adapter_id: str

def assign(self, experiment_id: UUID, unit_id: str) -> str:
    """Return the variant name this unit is assigned to (deterministic per unit)."""

def set_allocation(self, experiment_id: UUID, allocations: dict[str, float]) -> None:
    """Set allocation percentages per variant. The values must sum to 100."""

def kill(self, experiment_id: UUID) -> None:
    """Immediately route all traffic to control (control: 100)."""

def get_allocation(self, experiment_id: UUID) -> dict[str, float]:
    """Return the current allocation percentages per variant."""

def get_assignment_counts(self, experiment_id: UUID, since: datetime) -> dict[str, int]:
    """Return how many units have been assigned to each variant since `since` (for SRM)."""
```

`assign` must be **deterministic per unit** — the same unit always gets the same variant — and `get_assignment_counts` feeds the SRM check, so it must reflect actual assignments. The Postgres reference adapter assigns with SHA-256 of the unit id (Python's built-in `hash` is salted per process and would re-randomize across restarts).

### `MetricsSource` — the data warehouse (`ARCHITECTURE.md §11.2`)

Answers the only question the decision engine asks of the warehouse: for this metric, experiment, and window, what are the per-variant aggregated statistics? Defined in [`datatool/adapters/metrics/base.py`](../datatool/adapters/metrics/base.py). Reference implementations: `metrics/csv.py` (replay/testing), `metrics/posthog.py` (live).

```python
adapter_id: str

def query(
    self,
    metric: str,
    experiment_id: UUID,
    variant_split_by: str,
    window_start: datetime,
    window_end: datetime,
) -> list[Sample]:
    """Return per-variant aggregated samples for `metric` over the window."""

def supports_metric(self, metric: str) -> bool:
    """Whether this source can serve the named metric."""
```

**Optional: `CupedMetricsSource`.** A source that can also serve CUPED covariates implements `query_cuped(metric, experiment_id, variant_split_by, window_start, window_end, pre_period) -> CupedData`: per-arm cross-moments (`n, Σy, Σy², Σx, Σx², Σxy`, with `x` each observation's unit's mean over `[window_start − pre_period, window_start)`) plus unit-level pairs from the two pre-period windows for estimating θ. The controller checks for it when `statistics.enable_cuped` is on; see [statistics](statistics.md#cuped--variance-reduction-from-pre-period-data).

`query` returns [`Sample`](trust_contract.md#related-shapes-carry-a-contract-but-are-not-part-of-it) objects — `n`, `sum`, `sum_sq` per variant — which are exactly the running statistics the confidence sequence and guardrails consume, so the controller never touches raw events. The window is treated as half-open `[window_start, window_end)` by the bundled sources; a new source should match that so tiled windows don't double-count.

### `VariantSource` — variant materialization (`ARCHITECTURE.md §11.3`)

Turns a `VariantSpec` into the adapter-specific payload stored with the variant. Defined in [`datatool/adapters/variant/base.py`](../datatool/adapters/variant/base.py). Reference implementations: `variant/static.py`, `variant/llm.py`.

```python
adapter_id: str

def materialize(self, variant_spec: VariantSpec) -> dict:
    """Return the adapter-specific payload to store with the variant."""
```

Generation lives here, never in `core/` — the controller's job is to safely *run* an experiment, not to know how a variant came to exist (`ARCHITECTURE.md §20`, anti-scope). `materialize` is where scope is enforced: a variant that references a `forbidden_component` from the contract must be rejected with a structured error rather than persisted.

### `NotificationSink` — outbound events (`ARCHITECTURE.md §11.4`)

Delivers a structured controller event to wherever a team watches. Defined in [`datatool/adapters/notify/base.py`](../datatool/adapters/notify/base.py). Reference implementations: `notify/slack.py`, `notify/webhook.py`.

```python
adapter_id: str

def send(self, event_kind: str, payload: dict) -> None:
    """Deliver `payload` for an event of kind `event_kind` (e.g. 'revert')."""
```

The orchestrator emits events by kind and payload and does not care how or where they are rendered.

## A skeleton you can copy

The bundled adapters all follow the same shape — a module-level `ADAPTER_ID`, a class with that id and the protocol methods, and a `register()` the edge calls at startup. Here is the pattern for a metrics source (adapt the methods for the other three protocols):

```python
# datatool/adapters/metrics/mywarehouse.py
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from datatool.adapters import base as registry
from datatool.core.models import Sample

ADAPTER_ID = "metrics.mywarehouse"


class MyWarehouseMetricsSource:
    """MetricsSource backed by MyWarehouse."""

    adapter_id = ADAPTER_ID

    def __init__(self, dsn: str, variant_ids: dict[str, UUID]):
        # Construction is lazy — opening a connection here is fine; it runs when
        # the daemon wires the adapter up, not at import time.
        self._dsn = dsn
        self._variant_ids = dict(variant_ids)

    def query(
        self,
        metric: str,
        experiment_id: UUID,
        variant_split_by: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[Sample]:
        # Aggregate the half-open window [window_start, window_end) per variant and
        # return one Sample(n, sum, sum_sq) per arm.
        ...

    def supports_metric(self, metric: str) -> bool:
        ...


def register() -> None:
    """Register the factory under `metrics.mywarehouse`."""
    registry.register(ADAPTER_ID, MyWarehouseMetricsSource)
```

Two notes the bundled adapters illustrate:

- **Constructors differ by adapter.** The Postgres flag provider takes a session factory; the CSV metrics source takes a path plus a name→id mapping; the Slack sink takes a webhook URL. The registry stores the bare class; the wiring layer (the CLI/daemon) supplies the right constructor arguments per adapter. There is no uniform constructor signature.
- **An adapter may decline to register.** The LLM variant adapter's `register()` returns a `bool` and registers only when its optional extra (`anthropic`/`openai`, the `datatool[llm]` extra) is installed and a key is configured — a missing optional dependency is a no-op, not a crash.

## Wiring it in

Once the module exists, call its `register()` where the edge sets up adapters (the CLI/daemon startup path — see `datatool/cli/commands.py`). After that, anything that names your `adapter_id` in a contract's `source` field — a `goal.source`, a `guardrail.source` — resolves to your factory at runtime. Add it to the `doctor` checks if it should be part of the standard pre-flight.

## See also

- [`trust_contract.md`](trust_contract.md) — where `source` adapter ids are referenced in a contract.
- [`cli.md`](cli.md) — the commands that drive the adapters.
- [`statistics.md`](statistics.md) — what the `Sample` statistics a metrics source returns are used for.
