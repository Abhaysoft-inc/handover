"""Gemini provider: the same agent loop and structured calls as the Claude path,
on Google's Gen AI SDK. Chosen automatically when GEMINI_API_KEY is set and no
Claude key is (or force it with LLM_PROVIDER=gemini).

Gemini reads PDFs natively too (text and page images), so scanned and
non-English contracts still need no OCR step."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import uuid
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from .runtime import AgentStopped, RunCancelled, RunContext, ToolSpec, _strip_titles, invoke_tool

# preference order when GEMINI_MODEL=auto (and for overload fallbacks); the first one this key can use wins
PREFERRED = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3-flash",
             "gemini-2.5-flash", "gemini-3.1-pro-preview", "gemini-2.5-pro"]
SKIP = ("lite", "image", "tts", "live", "audio", "embedding", "computer", "transcribe", "banana", "omni")
_resolved: dict[str, str] = {}
_available: list[str] = []
_exhausted: set[str] = set()     # models whose free daily quota is used up (this process)


def make_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(
        timeout=600_000,
        # transient errors only; _generate handles 429 (rate limit vs. daily quota) and 503/504 (overload)
        # itself, because the right answer there is to wait the suggested delay or switch models
        retry_options=types.HttpRetryOptions(attempts=4, initial_delay=2.0, max_delay=30.0,
                                             http_status_codes=[408, 500, 502])))


async def _list_models(ctx: RunContext) -> list[str]:
    if not _available:
        try:
            async for m in await ctx.client.aio.models.list():
                name = (m.name or "").split("/")[-1]
                if name.startswith("gemini") and "generateContent" in (m.supported_actions or []):
                    _available.append(name)
        except Exception:  # listing is best effort; an explicit GEMINI_MODEL still works
            pass
    return _available


async def resolve_model(ctx: RunContext) -> str:
    if ctx.state.get("gemini_model"):
        return ctx.state["gemini_model"]
    wanted = ctx.settings.model
    names = await _list_models(ctx)
    if wanted and wanted != "auto":
        model = wanted
    else:
        if "auto" not in _resolved:
            _resolved["auto"] = (next((p for p in PREFERRED if p in names), None)
                                 or next((n for n in names if "flash" in n and not any(s in n for s in SKIP)), None)
                                 or "gemini-3.5-flash")
        model = _resolved["auto"]
    ctx.state["gemini_model"] = model
    return model


def _alternatives(current: str, tried: set[str]) -> list[str]:
    # flash first, then flash-lite variants as a last resort (each model has its own free quota)
    flash = [n for n in _available if "flash" in n and not any(s in n for s in SKIP)]
    lite = [n for n in _available if "flash-lite" in n and not any(s in n for s in SKIP if s != "lite")]
    order = PREFERRED + flash + lite
    return [m for m in dict.fromkeys(order)
            if m in _available and m not in tried and m != current and m not in _exhausted]


def _daily_quota(e: Exception) -> bool:
    text = (str(getattr(e, "message", "") or "") + str(getattr(e, "details", "") or "")).lower()
    return "per day" in text or "perday" in text.replace(" ", "")


def _retry_delay(e: Exception) -> float | None:
    m = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s", str(getattr(e, "details", "") or e))
    return float(m.group(1)) if m else None


# ── format conversion ─────────────────────────────────────────────────────


def _inline_refs(schema: dict) -> dict:
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs.get(node["$ref"].split("/")[-1], {}))
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def declarations(tools: list[ToolSpec]) -> list[types.FunctionDeclaration]:
    out = []
    for t in tools:
        schema = _inline_refs(_strip_titles(t.model.model_json_schema()))
        schema.setdefault("properties", {})
        out.append(types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=schema))
    return out


def to_parts(content: Any) -> list[types.Part]:
    """Claude-style content blocks (as built by documents.claude_blocks) → Gemini parts."""
    if isinstance(content, str):
        return [types.Part.from_text(text=content)]
    parts: list[types.Part] = []
    for b in content:
        kind = b.get("type")
        if kind == "text":
            parts.append(types.Part.from_text(text=b["text"]))
        elif kind in ("document", "image"):
            src = b.get("source", {})
            if b.get("title"):
                parts.append(types.Part.from_text(text=f"Attached document: {b['title']}"))
            if src.get("type") == "base64":
                parts.append(types.Part.from_bytes(data=base64.b64decode(src["data"]), mime_type=src["media_type"]))
            elif src.get("type") == "text":
                parts.append(types.Part.from_text(text=f"<document>\n{src.get('data', '')}\n</document>"))
    return parts


def _payload(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except ValueError:
            return content
    return content


# ── calls ─────────────────────────────────────────────────────────────────


def _config(ctx: RunContext, system: str, tools: list[ToolSpec] | None, schema: dict | None) -> types.GenerateContentConfig:
    cfg: dict[str, Any] = {"system_instruction": system}
    if tools:
        cfg["tools"] = [types.Tool(function_declarations=declarations(tools))]
        cfg["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
    if schema:
        cfg["response_mime_type"] = "application/json"
        cfg["response_json_schema"] = schema
    if ctx.state.get("gemini_thoughts", True):
        cfg["thinking_config"] = types.ThinkingConfig(include_thoughts=True)
    return types.GenerateContentConfig(**cfg)


async def _generate(ctx: RunContext, contents: list, *, system: str, tools: list[ToolSpec] | None = None,
                    schema: dict | None = None) -> types.GenerateContentResponse:
    tried: set[str] = set()
    waits = rate_waits = 0
    while True:
        ctx.check()
        model = await resolve_model(ctx)
        if ctx.state.get("model_announced") != model:
            ctx.state["model_announced"] = model
            ctx.emit("run.model", model=model, provider="gemini")
        try:
            resp = await ctx.client.aio.models.generate_content(model=model, contents=contents,
                                                                config=_config(ctx, system, tools, schema))
            break
        except genai_errors.ServerError as e:
            if e.code not in (503, 504):
                raise
            # overloaded ("high demand") or timed out: move to another model rather than wait it out
            tried.add(model)
            alts = _alternatives(model, tried)
            if alts:
                ctx.emit("agent.note", kind="system",
                         text=f"{model} is overloaded right now (HTTP {e.code}); switching to {alts[0]}.")
                ctx.state["gemini_model"] = alts[0]
                if ctx.settings.model == "auto":
                    _resolved["auto"] = alts[0]
                continue
            if waits >= 2:
                raise
            waits += 1
            tried.clear()
            await asyncio.sleep(20)
        except genai_errors.ClientError as e:
            # older / non-thinking models reject thought summaries: retry once without them
            if e.code == 400 and "thinking" in str(e).lower() and ctx.state.get("gemini_thoughts", True):
                ctx.state["gemini_thoughts"] = False
                continue
            if e.code == 429 and _daily_quota(e):
                # free tier: each model has its own daily request quota, so move on to another model
                _exhausted.add(model)
                tried.add(model)
                alts = _alternatives(model, tried)
                if alts:
                    ctx.emit("agent.note", kind="system",
                             text=f"{model} has used its free daily quota; switching to {alts[0]}.")
                    ctx.state["gemini_model"] = alts[0]
                    if ctx.settings.model == "auto":
                        _resolved["auto"] = alts[0]
                    continue
            elif e.code == 429 and rate_waits < 6:
                # per-minute limit: wait what the server suggests, then try again
                rate_waits += 1
                delay = min(90.0, _retry_delay(e) or 5.0 * 2 ** (rate_waits - 1))
                if rate_waits == 1:
                    ctx.emit("agent.note", kind="system", text=f"Rate limited by Gemini; retrying in {delay:.0f}s.")
                await asyncio.sleep(delay)
                continue
            raise
    u = resp.usage_metadata
    if u:
        cached = u.cached_content_token_count or 0
        ctx.usage["input_tokens"] += max(0, (u.prompt_token_count or 0) - cached)
        ctx.usage["cache_read_input_tokens"] += cached
        ctx.usage["output_tokens"] += (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)
    ctx.usage["requests"] += 1
    ctx.emit("usage", **ctx.usage, cost_usd=None)
    return resp


def _candidate(resp: types.GenerateContentResponse) -> types.Candidate:
    cand = resp.candidates[0] if resp.candidates else None
    if cand is None or cand.content is None or not cand.content.parts:
        block = resp.prompt_feedback.block_reason if resp.prompt_feedback else None
        reason = block or (cand.finish_reason if cand else None)
        if reason and str(reason).endswith("MALFORMED_FUNCTION_CALL"):
            raise ValueError("malformed function call")
        raise AgentStopped(f"Gemini returned no content ({reason or 'empty response'}).")
    return cand


async def run_agent(ctx: RunContext, *, system: str, user: str | list, tools: list[ToolSpec], max_turns: int = 40,
                    max_tokens: int = 64000, effort: str | None = None, stream_text: bool = False,
                    history: list[dict] | None = None) -> str:
    by_name = {t.name: t for t in tools}
    contents: list[types.Content] = [
        types.Content(role="user" if h["role"] == "user" else "model", parts=[types.Part.from_text(text=h["content"])])
        for h in history or []
    ]
    contents.append(types.Content(role="user", parts=to_parts(user)))
    final_text, retries = "", 0
    for _ in range(max_turns):
        ctx.check()
        try:
            resp = await _generate(ctx, contents, system=system, tools=tools)
            cand = _candidate(resp)
        except ValueError:
            retries += 1
            if retries > 2:
                raise AgentStopped("Gemini kept producing malformed tool calls.")
            ctx.emit("agent.note", kind="system", text="Re-issuing a turn whose tool call came back malformed.")
            continue
        retries = 0
        if str(cand.finish_reason).endswith(("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII")):
            raise AgentStopped(f"Gemini stopped for safety reasons ({cand.finish_reason}).")
        contents.append(cand.content)          # unmodified: keeps thought signatures for the next turn
        calls, texts = [], []
        for p in cand.content.parts or []:
            if p.function_call:
                calls.append(p.function_call)
            elif p.text and p.thought:
                note = re.sub(r"^\*\*(.+?)\*\*\s*", r"\1: ", p.text.strip())   # "**Title**\n\nbody" → "Title: body"
                ctx.emit("agent.note", kind="update", text=note if len(note) < 420 else note[:400].rsplit(" ", 1)[0] + "…")
            elif p.text:
                texts.append(p.text)
        text = "".join(texts).strip()
        if text:
            final_text = text
        if not calls:
            if stream_text and text:
                for i in range(0, len(text), 48):          # pseudo-stream the answer into the chat bubble
                    ctx.emit("agent.text_delta", text=text[i:i + 48])
                    await asyncio.sleep(0.01)
            elif text:
                ctx.emit("agent.note", kind="text", text=text)
            return final_text
        if text:
            ctx.emit("agent.note", kind="text", text=text)
        ids = [fc.id or f"gcall_{uuid.uuid4().hex[:10]}" for fc in calls]
        results = await asyncio.gather(*(invoke_tool(ctx, by_name.get(fc.name or ""), dict(fc.args or {}), cid,
                                                     fc.name or "?") for fc, cid in zip(calls, ids)))
        parts = []
        for fc, r in zip(calls, results):
            body = _payload(r["content"])
            parts.append(types.Part(function_response=types.FunctionResponse(
                id=fc.id, name=fc.name, response={"error": body} if r.get("is_error") else {"result": body})))
        contents.append(types.Content(role="user", parts=parts))
    raise AgentStopped(f"The agent hit its {max_turns}-turn limit before finishing.")


async def structured_call(ctx: RunContext, *, system: str, content: list, schema: dict, max_tokens: int = 24000,
                          effort: str | None = None) -> dict:
    for attempt in range(2):
        resp = await _generate(ctx, [types.Content(role="user", parts=to_parts(content))], system=system,
                               schema=schema)
        try:
            cand = _candidate(resp)
        except ValueError:
            continue
        text = "".join(p.text for p in cand.content.parts if p.text and not p.thought)
        try:
            return json.loads(text)
        except ValueError:
            if attempt == 1 or str(cand.finish_reason).endswith("MAX_TOKENS"):
                raise RuntimeError("Gemini's JSON output was cut off or invalid.")
    raise RuntimeError("Gemini did not return a usable structured answer.")


def error_text(e: BaseException) -> str | None:
    if not isinstance(e, genai_errors.APIError):
        return None
    msg = getattr(e, "message", None) or str(e)
    if e.code in (401, 403) or "API key" in msg:
        return "Gemini rejected the API key. Check GEMINI_API_KEY in .env (or switch to Simulation mode)."
    if e.code == 404:
        return f"Gemini model not found: {msg}. Set GEMINI_MODEL in .env (or leave it as auto)."
    if e.code == 429:
        if "perday" in msg.replace(" ", "").lower() or "per day" in msg.lower():
            return "Gemini's daily free-tier quota for this model is used up. Try again tomorrow, set GEMINI_MODEL " \
                   "to another model, or use a paid key."
        return "Gemini rate limit reached (free-tier keys allow only a few requests per minute). Wait a minute, " \
               "lower ANALYST_CONCURRENCY to 1-2, or use a paid key."
    if e.code in (503, 504):
        return "Gemini is overloaded right now (every available Flash model said 'high demand'). Try again in a " \
               "few minutes."
    return f"Gemini API error {e.code}: {msg}"


__all__ = ["make_client", "run_agent", "structured_call", "error_text", "resolve_model", "RunCancelled"]
