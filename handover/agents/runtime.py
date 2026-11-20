"""Agent runtime: run lifecycle, live event stream, tool invocation, and the
streaming tool-use loop on the Claude API.

Every run (live or simulated) emits the same events, so the dashboard renders a
Claude-driven run and an offline simulation identically."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import anthropic
from pydantic import BaseModel, ValidationError

from .. import db
from ..config import PRICES, Settings, get_settings
from ..events import bus, changed

log = logging.getLogger("handover.agents")

# server-side refusal fallback + readable progress updates between tool calls
AGENT_BETAS = ["server-side-fallback-2026-07-01", "thinking-display-updates-2026-08-18"]
CALL_BETAS = ["server-side-fallback-2026-07-01"]


class RunCancelled(Exception):
    pass


class AgentStopped(Exception):
    pass


@dataclass
class ToolOutput:
    content: Any                 # str, or list of content blocks
    summary: str                 # one line for the activity feed
    is_error: bool = False


@dataclass
class ToolSpec:
    name: str
    description: str
    model: type[BaseModel]
    handler: Callable[["RunContext", Any], Awaitable[ToolOutput]]
    label: Callable[[dict], str]

    def definition(self) -> dict:
        schema = _strip_titles(self.model.model_json_schema())
        schema.setdefault("properties", {})
        return {"name": self.name, "description": self.description, "input_schema": schema,
                "eager_input_streaming": True}


def _strip_titles(node: Any, in_properties: bool = False) -> Any:
    """Drop pydantic's "title" annotations, but never a property that is *named* title."""
    if isinstance(node, dict):
        return {k: _strip_titles(v, in_properties=(k == "properties" and not in_properties))
                for k, v in node.items()
                if in_properties or not (k == "title" and isinstance(v, str))}
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


@dataclass
class RunContext:
    run_id: str
    kind: str
    mode: str
    settings: Settings
    client: Any = None          # anthropic.AsyncAnthropic, or google.genai.Client for Gemini
    seq: int = 0
    cancelled: bool = False
    state: dict = field(default_factory=dict)
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0,
                                                 "cache_creation_input_tokens": 0,
                                                 "cache_read_input_tokens": 0, "requests": 0})

    def emit(self, type_: str, **data: Any) -> None:
        self.seq += 1
        ts = db.now_iso()
        db.insert("run_events", {"run_id": self.run_id, "seq": self.seq, "ts": ts, "type": type_,
                                 "data_json": json.dumps(data, ensure_ascii=False, default=str)})
        bus.publish({"run_id": self.run_id, "kind": self.kind, "seq": self.seq, "ts": ts, "type": type_,
                     "data": data})

    def check(self) -> None:
        if self.cancelled:
            raise RunCancelled()

    def add_usage(self, usage: Any) -> None:
        if usage is None:
            return
        for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            self.usage[k] += int(getattr(usage, k, 0) or 0)
        self.usage["requests"] += 1
        self.emit("usage", **self.usage, cost_usd=self.cost())

    def cost(self) -> float | None:
        if self.settings.provider != "anthropic":
            return None          # Gemini pricing varies by model and tier; the UI shows tokens instead
        u = self.usage
        return round((u["input_tokens"] * PRICES["input"] + u["output_tokens"] * PRICES["output"]
                      + u["cache_creation_input_tokens"] * PRICES["cache_write"]
                      + u["cache_read_input_tokens"] * PRICES["cache_read"]) / 1_000_000, 4)

    @contextlib.asynccontextmanager
    async def step(self, name: str, label: str, input_: dict | None = None):
        """Show a non-tool unit of work (e.g. one draft) in the activity feed."""
        call_id = "step_" + uuid.uuid4().hex[:10]
        self.emit("tool.started", call_id=call_id, name=name, label=label, input=input_ or {})
        result = {"summary": "", "ok": True}
        try:
            yield result
        except Exception as e:
            self.emit("tool.finished", call_id=call_id, name=name, ok=False, summary=str(e)[:240])
            raise
        else:
            self.emit("tool.finished", call_id=call_id, name=name, ok=result["ok"], summary=result["summary"])


COMPAT = ("groq", "openai_compat")


def make_client(s: Settings) -> Any:
    if s.provider == "gemini":
        from . import gemini

        return gemini.make_client(s.gemini_api_key)
    if s.provider in COMPAT:
        from . import openai_compat

        return openai_compat.make_client(s.compat)
    kwargs: dict[str, Any] = {"max_retries": 4, "timeout": 900.0}
    if s.api_key:
        kwargs["api_key"] = s.api_key
    elif s.auth_token:
        kwargs["auth_token"] = s.auth_token
    return anthropic.AsyncAnthropic(**kwargs)


# ── run lifecycle ─────────────────────────────────────────────────────────

_tasks: dict[str, asyncio.Task] = {}
_contexts: dict[str, RunContext] = {}
EXCLUSIVE = {"discovery", "drafting", "execution"}


def active_runs() -> list[dict]:
    return [{"run_id": rid, "kind": c.kind, "mode": c.mode} for rid, c in _contexts.items()]


def busy_with(kinds: set[str]) -> str | None:
    for rid, c in _contexts.items():
        if c.kind in kinds:
            return rid
    return None


def _error_text(e: BaseException) -> str:
    s = get_settings()
    if s.provider == "gemini":
        from . import gemini

        text = gemini.error_text(e)
        if text:
            return text
    if s.provider in COMPAT and s.compat:
        from . import openai_compat

        text = openai_compat.error_text(e, s.compat.label)
        if text:
            return text
    if isinstance(e, anthropic.AuthenticationError):
        return "Anthropic rejected the API key. Check ANTHROPIC_API_KEY in .env (or switch to Simulation mode)."
    if isinstance(e, anthropic.PermissionDeniedError):
        return "This API key lacks permission for the model or a beta feature in use."
    if isinstance(e, anthropic.NotFoundError):
        return f"Model not found: {get_settings().model}. Set HANDOVER_MODEL in .env."
    if isinstance(e, anthropic.RateLimitError):
        return "Rate limited by the Anthropic API — wait a minute and retry (or lower ANALYST_CONCURRENCY)."
    if isinstance(e, anthropic.BadRequestError):
        return f"The API rejected a request: {getattr(e, 'message', e)}"
    if isinstance(e, anthropic.APIStatusError):
        return f"Anthropic API error {e.status_code}: {getattr(e, 'message', e)}"
    if isinstance(e, anthropic.APIConnectionError):
        return "Could not reach the Anthropic API — check the network connection."
    return f"{type(e).__name__}: {e}"


def start_run(kind: str, title: str, work: Callable[[RunContext], Awaitable[str]],
              force_mode: str | None = None) -> str:
    s = get_settings()
    mode = force_mode or s.mode
    run_id = "R-" + uuid.uuid4().hex[:8]
    ctx = RunContext(run_id=run_id, kind=kind, mode=mode, settings=s)
    if mode == "live":
        ctx.client = make_client(s)
    db.insert("runs", {"id": run_id, "kind": kind, "status": "running", "mode": mode, "title": title,
                       "started_at": db.now_iso()})
    _contexts[run_id] = ctx
    ctx.emit("run.started", kind=kind, title=title, mode=mode, model=s.model if mode == "live" else "simulation")
    changed("runs")

    async def runner() -> None:
        status, summary, error = "completed", "", None
        try:
            summary = await work(ctx) or ""
        except (RunCancelled, asyncio.CancelledError):
            status, summary = "cancelled", "Stopped by counsel."
        except AgentStopped as e:
            status, error = "failed", str(e)
        except Exception as e:  # surface every failure in the UI
            log.exception("run %s failed", run_id)
            status, error = "failed", _error_text(e)
        finally:
            db.update("runs", run_id, {"status": status, "finished_at": db.now_iso(), "summary": summary,
                                       "error": error, "usage_json": json.dumps({**ctx.usage, "cost_usd": ctx.cost()})})
            ctx.emit(f"run.{status}", summary=summary, error=error, usage={**ctx.usage, "cost_usd": ctx.cost()})
            _contexts.pop(run_id, None)
            _tasks.pop(run_id, None)
            changed("runs", "overview")

    _tasks[run_id] = asyncio.create_task(runner())
    return run_id


def cancel_run(run_id: str) -> bool:
    ctx = _contexts.get(run_id)
    if not ctx:
        return False
    ctx.cancelled = True
    task = _tasks.get(run_id)
    if task:
        task.cancel()
    return True


# ── tools ─────────────────────────────────────────────────────────────────


def _preview(value: Any, limit: int = 600) -> Any:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return value if len(text) <= limit else {"_preview": text[:limit] + "…"}


async def invoke_tool(ctx: RunContext, spec: ToolSpec | None, raw: Any, call_id: str, name: str) -> dict:
    if spec is None:
        ctx.emit("tool.finished", call_id=call_id, name=name, ok=False, summary="Unknown tool")
        return {"type": "tool_result", "tool_use_id": call_id, "is_error": True, "content": f"Unknown tool {name}"}
    try:
        args = spec.model.model_validate(raw if isinstance(raw, dict) else {})
    except ValidationError as e:
        ctx.emit("tool.finished", call_id=call_id, name=name, ok=False, summary="Rejected malformed input")
        payload = {"INVALID_JSON": json.dumps(raw, ensure_ascii=False, default=str)[:3000],
                   "errors": [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()][:6]}
        return {"type": "tool_result", "tool_use_id": call_id, "is_error": True, "content": json.dumps(payload)}
    try:
        label = spec.label(raw if isinstance(raw, dict) else {})
    except Exception:
        label = name
    ctx.emit("tool.started", call_id=call_id, name=name, label=label, input=_preview(raw))
    try:
        out = await spec.handler(ctx, args)
    except (RunCancelled, asyncio.CancelledError):
        raise
    except Exception as e:
        log.exception("tool %s failed", name)
        out = ToolOutput(content=f"Error: {e}", summary=str(e)[:240], is_error=True)
    ctx.emit("tool.finished", call_id=call_id, name=name, ok=not out.is_error, summary=out.summary)
    result = {"type": "tool_result", "tool_use_id": call_id, "content": out.content}
    if out.is_error:
        result["is_error"] = True
    return result


async def call_tool(ctx: RunContext, tools: list[ToolSpec], name: str, args: dict) -> dict:
    """Used by the simulator: run a tool exactly as the live agent would."""
    spec = next((t for t in tools if t.name == name), None)
    return await invoke_tool(ctx, spec, args, "sim_" + uuid.uuid4().hex[:10], name)


# ── the live agent loop ───────────────────────────────────────────────────


async def _stream_turn(ctx: RunContext, *, system: str, messages: list, tools: list[dict], max_tokens: int,
                       effort: str, stream_text: bool) -> Any:
    s = ctx.settings
    blocks: dict[int, dict] = {}
    async with ctx.client.beta.messages.stream(
        model=s.model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        tools=tools,
        thinking={"type": "adaptive", "display": "updates"},
        output_config={"effort": effort},
        cache_control={"type": "ephemeral"},
        betas=AGENT_BETAS,
        fallbacks="default",
    ) as stream:
        async for event in stream:
            if ctx.cancelled:
                raise RunCancelled()
            et = event.type
            if et == "content_block_start":
                cb = event.content_block
                blocks[event.index] = {"type": cb.type, "buf": []}
                if cb.type == "tool_use":
                    ctx.emit("tool.pending", call_id=cb.id, name=cb.name)
                elif cb.type == "fallback":
                    src = getattr(getattr(cb, "from_", None), "model", "?")
                    dst = getattr(getattr(cb, "to", None), "model", "?")
                    ctx.emit("agent.note", kind="system", text=f"Safety fallback: {src} declined, {dst} continued.")
            elif et == "content_block_delta":
                b = blocks.get(event.index)
                d = event.delta
                if b is None:
                    continue
                if d.type == "thinking_delta":
                    b["buf"].append(d.thinking)
                elif d.type == "text_delta":
                    b["buf"].append(d.text)
                    if stream_text:
                        ctx.emit("agent.text_delta", text=d.text)
            elif et == "content_block_stop":
                b = blocks.get(event.index)
                if b and b["type"] in ("thinking", "text"):
                    text = "".join(b["buf"]).strip()
                    if text and not (b["type"] == "text" and stream_text):
                        ctx.emit("agent.note", kind="update" if b["type"] == "thinking" else "text", text=text)
        message = await stream.get_final_message()
    ctx.add_usage(message.usage)
    return message


async def run_agent(ctx: RunContext, *, system: str, user: str | list, tools: list[ToolSpec],
                    max_turns: int = 40, max_tokens: int = 64000, effort: str | None = None,
                    stream_text: bool = False, history: list[dict] | None = None) -> str:
    """Stream turns until Claude stops calling tools. Appends every assistant turn
    unmodified (thinking blocks included) and never edits history."""
    if ctx.settings.provider == "gemini":
        from . import gemini

        return await gemini.run_agent(ctx, system=system, user=user, tools=tools, max_turns=max_turns,
                                      max_tokens=max_tokens, effort=effort, stream_text=stream_text, history=history)
    if ctx.settings.provider in COMPAT:
        from . import openai_compat

        return await openai_compat.run_agent(ctx, system=system, user=user, tools=tools, max_turns=max_turns,
                                             max_tokens=max_tokens, effort=effort, stream_text=stream_text,
                                             history=history)
    by_name = {t.name: t for t in tools}
    defs = [t.definition() for t in tools]
    messages: list[dict] = [*(history or []), {"role": "user", "content": user}]
    final_text, json_retries = "", 0
    for _ in range(max_turns):
        ctx.check()
        try:
            message = await _stream_turn(ctx, system=system, messages=messages, tools=defs, max_tokens=max_tokens,
                                         effort=effort or ctx.settings.effort, stream_text=stream_text)
        except ValueError:
            # eager tool-input streaming: JSON the SDK could not parse at all → re-issue the turn
            json_retries += 1
            if json_retries > 2:
                raise
            ctx.emit("agent.note", kind="system", text="Re-issuing a turn whose tool input arrived garbled.")
            continue
        json_retries = 0
        messages.append({"role": "assistant", "content": message.content})
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise AgentStopped(f"Claude declined to continue (refusal{f': {category}' if category else ''}).")
        texts = "".join(b.text for b in message.content if b.type == "text").strip()
        if texts:
            final_text = texts
        tool_uses = [b for b in message.content if b.type == "tool_use"]
        if not tool_uses:
            if message.stop_reason == "pause_turn":
                continue
            return final_text
        if message.stop_reason == "max_tokens":
            results = [{"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                        "content": "This tool call was cut off by the output limit. Send it again with shorter "
                                   "inputs (split large batches)."} for b in tool_uses]
        else:
            results = list(await asyncio.gather(
                *(invoke_tool(ctx, by_name.get(b.name), b.input, b.id, b.name) for b in tool_uses)))
        messages.append({"role": "user", "content": results})
    raise AgentStopped(f"The agent hit its {max_turns}-turn limit before finishing.")


async def structured_call(ctx: RunContext, *, system: str, content: list, schema: dict,
                          max_tokens: int = 24000, effort: str | None = None) -> dict:
    """One model call constrained to a JSON schema (structured outputs)."""
    if ctx.settings.provider == "gemini":
        from . import gemini

        return await gemini.structured_call(ctx, system=system, content=content, schema=schema,
                                            max_tokens=max_tokens, effort=effort)
    if ctx.settings.provider in COMPAT:
        from . import openai_compat

        return await openai_compat.structured_call(ctx, system=system, content=content, schema=schema,
                                                   max_tokens=max_tokens, effort=effort)
    s = ctx.settings
    async with ctx.client.beta.messages.stream(
        model=s.model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort or s.effort, "format": {"type": "json_schema", "schema": schema}},
        betas=CALL_BETAS,
        fallbacks="default",
    ) as stream:
        message = await stream.get_final_message()
    ctx.add_usage(message.usage)
    if message.stop_reason == "refusal":
        raise AgentStopped("Claude declined this document (refusal).")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("The response hit the output limit before the JSON was complete.")
    text = "".join(b.text for b in message.content if b.type == "text")
    return json.loads(text)
