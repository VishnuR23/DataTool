"""The conversational assistant — the LLM interface layer over the operations spine.

The assistant is **interface + narration only**. It never decides ramp / promote /
revert; the deterministic stats + trust contract do (the project invariant). It exposes
a small, fixed tool set mapped 1:1 to ``control/operations.py``:

- **Reads** (``list_experiments``, ``status``, ``why``) run freely — they only observe.
- **Mutating** tools (``pause``, ``resume``, ``promote``, ``revert``) each route through
  the trust-contract-clamped, audited operations, and only *after* the operator confirms
  them. This mirrors DataTool's "every action is clamped and logged" ethos: the LLM can
  propose an action, but a human authorises it before the control plane executes it.

The loop here is provider-neutral. A :class:`ChatClient` translates the neutral transcript
to and from a concrete vendor (see :class:`AnthropicChatClient`); tests drive it with a
fake client. With no LLM key the TUI still runs — the assistant is an enhancement, not a
hard dependency (see :func:`default_chat_client_from_env`).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from datatool.config import get_settings
from datatool.control import operations
from datatool.core.exceptions import DataToolError
from datatool.persistence.db import session_scope

ACTOR = "assistant"

# --------------------------------------------------------------------------- #
# Neutral conversation types — the lingua franca between the loop and a client
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ToolCall:
    """A request from the model to run one tool with structured arguments."""

    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class ToolResult:
    """The outcome of running one tool, fed back to the model on the next turn."""

    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class UserTurn:
    text: str


@dataclass(frozen=True)
class AssistantTurn:
    """The model's reply: narration text plus zero or more tool calls."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    # DECISION: carry the provider's own content blocks so a client can replay them
    # verbatim. Thinking blocks must go back unchanged in a tool-use loop, and the
    # neutral text/tool_calls view cannot reconstruct them. None = rebuild from the view.
    raw: list | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class ToolResultTurn:
    """A synthetic user turn carrying the results of the model's tool calls."""

    results: list[ToolResult]


Transcript = list  # list[UserTurn | AssistantTurn | ToolResultTurn]


class ChatClient(Protocol):
    """Minimal interface the loop needs: neutral transcript in, one assistant turn out."""

    def reply(self, *, system: str, transcript: Transcript, tools: list[dict]) -> AssistantTurn: ...


# --------------------------------------------------------------------------- #
# Tools — the fixed set mapped 1:1 to control/operations.py
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], str]
    mutating: bool

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def run(self, arguments: dict) -> str:
        return self.handler(arguments)


_NAME_OR_ID = {
    "type": "object",
    "properties": {"name_or_id": {"type": "string", "description": "The experiment name or UUID."}},
    "required": ["name_or_id"],
}


def build_tools(
    session_factory: sessionmaker[Session],
    *,
    config_dir: str = "./config",
    variant_client=None,
) -> dict[str, Tool]:
    """Construct the fixed tool set bound to a session factory.

    ``config_dir`` supplies org/surface defaults for ``register``. ``variant_client``
    overrides the LLM client used by ``generate_variant`` (defaults to one built from
    the environment); tests inject a fake.
    """

    def _list(_: dict) -> str:
        with session_scope(session_factory) as session:
            rows = operations.ExperimentRepository(session).list()
            if not rows:
                return "no experiments registered."
            return "\n".join(f"- {e.name} [{e.state}] on {e.surface}" for e in rows)

    def _status(args: dict) -> str:
        with session_scope(session_factory) as session:
            exp = operations._require(session, args["name_or_id"])
            pct = operations.treatment_pct(session, exp)
            return f"{exp.name}: {exp.state}, treatment at {pct:g}%"

    def _why(args: dict) -> str:
        with session_scope(session_factory) as session:
            exp = operations._require(session, args["name_or_id"])
            events = operations.decision_log(session, exp)
            if not events:
                return f"{exp.name}: no decisions or actions recorded yet."
            lines = [f"why {exp.name}:"]
            lines += [f"  {kind}: {line}" for _, kind, line in events]
            return "\n".join(lines)

    def _pause(args: dict) -> str:
        name = operations.pause(session_factory, args["name_or_id"], actor=ACTOR)
        return f"paused {name} (now holding)."

    def _resume(args: dict) -> str:
        name = operations.resume(session_factory, args["name_or_id"], actor=ACTOR)
        return f"resumed {name} (now ramping)."

    def _promote(args: dict) -> str:
        name = operations.promote(
            session_factory, args["name_or_id"], force=bool(args.get("force", False)), actor=ACTOR
        )
        return f"promoted {name} to full rollout."

    def _revert(args: dict) -> str:
        name = operations.revert(
            session_factory, args["name_or_id"], args.get("reason"), actor=ACTOR
        )
        return f"reverted {name}."

    def _register(args: dict) -> str:
        import yaml

        data = yaml.safe_load(args["definition"])
        if not isinstance(data, dict):
            raise DataToolError("register 'definition' must be a YAML experiment mapping.")
        name = operations.register(session_factory, data=data, config_dir=config_dir, actor=ACTOR)
        return f"registered {name} (state: proposed)."

    def _generate_variant(args: dict) -> str:
        from datatool.adapters.variant.llm import (
            LLMVariantCache,
            LLMVariantSource,
            default_client_from_env,
        )
        from datatool.core.models import VariantSpec

        with session_scope(session_factory) as session:
            exp = operations._require(session, args["name_or_id"])
            exp_name = exp.name
            # The declared scope is enough to enforce the ban; no need to resolve
            # and validate the full effective contract just to read one list.
            forbidden = (exp.contract or {}).get("scope", {}).get("forbidden_components") or []
        source = LLMVariantSource(
            client=variant_client or default_client_from_env(),
            forbidden_components=forbidden,
            cache=LLMVariantCache(session_factory),
        )
        spec = VariantSpec(
            name="assistant-generated",
            source="llm",
            payload={
                "surface_description": args["surface_description"],
                "extra_instructions": args.get("extra_instructions", ""),
            },
        )
        result = source.materialize(spec)
        return (
            f"candidate variant for {exp_name}:\n"
            f"summary: {result['summary']}\n"
            f"rationale: {result['rationale']}"
        )

    _revert_schema = {
        "type": "object",
        "properties": {
            "name_or_id": {"type": "string", "description": "The experiment name or UUID."},
            "reason": {"type": "string", "description": "Why the experiment is being reverted."},
        },
        "required": ["name_or_id"],
    }
    _promote_schema = {
        "type": "object",
        "properties": {
            "name_or_id": {"type": "string", "description": "The experiment name or UUID."},
            "force": {
                "type": "boolean",
                "description": "Promote even if not holding for approval.",
            },
        },
        "required": ["name_or_id"],
    }
    _register_schema = {
        "type": "object",
        "properties": {
            "definition": {
                "type": "string",
                "description": (
                    "The full experiment definition as YAML (experiment, surface, owner, "
                    "variants, and a contract block), the same shape as a register file."
                ),
            }
        },
        "required": ["definition"],
    }
    _generate_variant_schema = {
        "type": "object",
        "properties": {
            "name_or_id": {
                "type": "string",
                "description": "The experiment whose contract scope constrains the variant.",
            },
            "surface_description": {
                "type": "string",
                "description": "Plain-English description of the change to generate.",
            },
            "extra_instructions": {
                "type": "string",
                "description": "Optional extra guidance for the generator.",
            },
        },
        "required": ["name_or_id", "surface_description"],
    }

    tools = [
        Tool(
            "list_experiments",
            "List every experiment and its lifecycle state.",
            {"type": "object", "properties": {}},
            _list,
            mutating=False,
        ),
        Tool(
            "status",
            "Show one experiment's state and current treatment allocation.",
            _NAME_OR_ID,
            _status,
            mutating=False,
        ),
        Tool(
            "why",
            "Explain the decisions and actions taken for an experiment, from the audit log.",
            _NAME_OR_ID,
            _why,
            mutating=False,
        ),
        Tool(
            "generate_variant",
            "Generate a candidate variant for an experiment from a plain-English "
            "description, enforcing the contract's forbidden components. Does not ship it.",
            _generate_variant_schema,
            _generate_variant,
            mutating=False,
        ),
        Tool(
            "register",
            "Register a new experiment from a YAML definition (starts in the proposed state).",
            _register_schema,
            _register,
            mutating=True,
        ),
        Tool(
            "pause",
            "Pause an experiment (move it to holding so it will not ramp further).",
            _NAME_OR_ID,
            _pause,
            mutating=True,
        ),
        Tool(
            "resume",
            "Resume a paused experiment (holding back to ramping).",
            _NAME_OR_ID,
            _resume,
            mutating=True,
        ),
        Tool(
            "promote",
            "Approve full rollout for an experiment (ship at 100%).",
            _promote_schema,
            _promote,
            mutating=True,
        ),
        Tool(
            "revert",
            "Revert an experiment (kill it back to control).",
            _revert_schema,
            _revert,
            mutating=True,
        ),
    ]
    return {t.name: t for t in tools}


# --------------------------------------------------------------------------- #
# The assistant loop
# --------------------------------------------------------------------------- #


SYSTEM_PROMPT = (
    "You are DataTool's terminal assistant. DataTool is an autonomous experimentation "
    "controller: a deterministic control plane (statistics + a trust contract) decides "
    "when experiments ramp, hold, promote, or revert. You are the operator's interface to "
    "it — you never make those decisions yourself. Use the read tools to answer questions "
    "and narrate what the control plane did and why, grounding every claim in the audit "
    "log. Use the mutating tools only to carry out an action the operator asked for; the "
    "operator must confirm each one before it runs. Be concise and use sentence case."
)


class Assistant:
    """Drives the tool-use loop: model proposes tools, the gate authorises mutating ones."""

    def __init__(
        self,
        client: ChatClient,
        tools: dict[str, Tool],
        *,
        confirm: Callable[[ToolCall], bool] = lambda call: False,
        system: str = SYSTEM_PROMPT,
        max_steps: int = 12,
    ) -> None:
        self._client = client
        self._tools = tools
        self._confirm = confirm
        self._system = system
        self._max_steps = max_steps
        self.transcript: Transcript = []

    def send(self, user_input: str) -> str:
        """Run one operator message to completion; return the assistant's final text."""
        self.transcript.append(UserTurn(user_input))
        schemas = [t.schema() for t in self._tools.values()]

        for _ in range(self._max_steps):
            turn = self._client.reply(
                system=self._system, transcript=self.transcript, tools=schemas
            )
            self.transcript.append(turn)
            if not turn.tool_calls:
                return turn.text
            self.transcript.append(ToolResultTurn([self._run(call) for call in turn.tool_calls]))

        return "stopped: reached the tool step limit without a final answer."

    def _run(self, call: ToolCall) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(call.id, f"unknown tool {call.name!r}.", is_error=True)
        if tool.mutating and not self._confirm(call):
            return ToolResult(call.id, "operator declined this action.")
        try:
            return ToolResult(call.id, tool.run(call.input))
        except DataToolError as exc:
            return ToolResult(call.id, f"could not complete: {exc}", is_error=True)
        except Exception as exc:  # never let a tool bug crash the console
            return ToolResult(call.id, f"error: {exc}", is_error=True)


# --------------------------------------------------------------------------- #
# Anthropic client — lazy, optional, env-key (reuses the variant adapter's pattern)
# --------------------------------------------------------------------------- #


class AnthropicChatClient:
    """A ChatClient backed by the Anthropic Messages API with tool use (lazy import)."""

    def __init__(self, *, api_key: str, model: str, max_tokens: int = 16000) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise DataToolError(
                "the 'anthropic' package is required for the assistant; "
                "install with: pip install datatool[llm]"
            ) from exc
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    def reply(self, *, system: str, transcript: Transcript, tools: list[dict]) -> AssistantTurn:
        rendered = (_to_anthropic(entry) for entry in transcript)
        message = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            tools=tools,
            messages=[m for m in rendered if m is not None],
        )
        if message.stop_reason == "refusal":
            # Nothing to replay: the turn drops out of later requests, and the API
            # merges the surrounding user turns.
            return AssistantTurn(text="the model declined to answer that request.", raw=[])
        text_parts, tool_calls = [], []
        for block in message.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input)))
        return AssistantTurn(
            text="".join(text_parts), tool_calls=tool_calls, raw=list(message.content)
        )


def _to_anthropic(entry: object) -> dict | None:
    """Render one neutral transcript entry as an Anthropic message (None = omit it)."""
    if isinstance(entry, UserTurn):
        return {"role": "user", "content": entry.text}
    if isinstance(entry, AssistantTurn):
        if entry.raw is not None:
            return {"role": "assistant", "content": entry.raw} if entry.raw else None
        content: list[dict] = []
        if entry.text:
            content.append({"type": "text", "text": entry.text})
        content += [
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input}
            for c in entry.tool_calls
        ]
        return {"role": "assistant", "content": content}
    if isinstance(entry, ToolResultTurn):
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": r.tool_use_id,
                    "content": r.content,
                    "is_error": r.is_error,
                }
                for r in entry.results
            ],
        }
    raise TypeError(f"unknown transcript entry {type(entry).__name__}")


def default_chat_client_from_env() -> ChatClient | None:
    """Build an Anthropic chat client if a key is set, else ``None`` (assistant disabled).

    # DECISION: Anthropic only for the tool-use assistant in this phase; OpenAI tool
    # calling is a follow-up. The variant adapter still supports both for generation.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return AnthropicChatClient(api_key=api_key, model=get_settings().llm_default_model)
