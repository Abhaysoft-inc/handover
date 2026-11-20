"""OpenAI-compatible provider: Groq by default (free tier), and anything else that
speaks the Chat Completions API (OpenRouter, Cerebras, a local Ollama, ...).

Same tools, guardrails and UI as the Claude and Gemini paths. Three things keep it
inside small free-tier limits (Groq: ~8K tokens per minute per model):
  * a client-side token budget per model paces requests instead of tripping 429s,
  * old tool results are trimmed as a run grows, so each request stays small,
  * document analysis is spread round-robin over several models (each has its own
    limits), and scanned pages are transcribed one page per request by a vision model."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import itertools
import json
import re
import time
from collections import defaultdict, deque
from typing import Any

import openai
from openai import AsyncOpenAI

from ..config import CompatConfig
from ..documents import extract_text
from .runtime import AgentStopped, RunContext, ToolSpec, _strip_titles, invoke_tool

THINK = re.compile(r"<think>.*?</think>", re.S)
IMAGE_TOKENS = 2048          # Groq counts each image as 2048 input tokens
WINDOW = 60.0                # seconds in a rate-limit window (tests shorten it)


class TooLarge(Exception):
    pass


class ToolCallRejected(Exception):
    """The provider refused the model's tool call (e.g. Groq validates arguments server-side)."""


REJECTED_TOOL_CALL = ("tool call validation failed", "tool_use_failed", "failed to call a function")


def make_client(c: CompatConfig) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=c.api_key, base_url=c.base_url, max_retries=2, timeout=300.0)


# ── token accounting ──────────────────────────────────────────────────────


def estimate_tokens(obj: Any) -> int:
    images = 0

    def walk(o: Any) -> int:
        nonlocal images
        if isinstance(o, dict):
            if o.get("type") == "image_url":
                images += 1
                return 0
            return sum(walk(v) for v in o.values())
        if isinstance(o, (list, tuple)):
            return sum(walk(v) for v in o)
        if isinstance(o, str):
            return len(o)
        return 4

    chars = walk(obj)
    return int(chars / 3.4) + images * IMAGE_TOKENS + 8


class _Budget:
    """Sliding 60-second token window per model."""

    def __init__(self) -> None:
        self.log: dict[str, deque] = defaultdict(deque)

    async def take(self, ctx: RunContext, model: str, tokens: int, tpm: int, label: str) -> list | None:
        if tpm <= 0:
            return None
        tokens = min(tokens, tpm)
        while True:
            now = time.monotonic()
            q = self.log[model]
            while q and now - q[0][0] >= WINDOW:
                q.popleft()
            if sum(e[1] for e in q) + tokens <= tpm:
                entry = [now, tokens]
                q.append(entry)
                return entry
            wait = WINDOW + 0.2 - (now - q[0][0])
            if wait > 3 and not ctx.state.get("budget_note"):
                ctx.state["budget_note"] = True
                ctx.emit("agent.note", kind="system",
                         text=f"Pacing requests to stay inside {label}'s free per-minute token budget.")
            ctx.check()
            await asyncio.sleep(min(max(wait, 0.5), 10))


_budget = _Budget()
_analyst_cycle: dict[tuple, Any] = {}
_transcripts: dict[str, str] = {}


def _next_analyst(c: CompatConfig) -> str:
    key = c.analyst_models or (c.agent_model,)
    if key not in _analyst_cycle:
        _analyst_cycle[key] = itertools.cycle(key)
    return next(_analyst_cycle[key])


# ── one request ───────────────────────────────────────────────────────────


def _reasoning_params(c: CompatConfig, model: str, effort: str) -> tuple[dict, dict]:
    """(kwargs, extra_body) for Groq's reasoning models; nothing for other endpoints."""
    if not c.groq:
        return {}, {}
    if "gpt-oss" in model:
        return {"reasoning_effort": effort if effort in ("low", "medium", "high") else "low"}, {}
    if "qwen" in model:
        # parsed keeps <think> out of the content; "default" = no reasoning tokens on Groq
        return {"reasoning_effort": effort if effort in ("none", "default", "low", "medium", "high") else "default"}, \
               {"reasoning_format": "parsed"}
    return {}, {}


async def _chat(ctx: RunContext, model: str, messages: list, *, tools: list | None = None,
                schema: dict | None = None, json_object: bool = False, max_tokens: int = 2000,
                effort: str = "low") -> Any:
    c: CompatConfig = ctx.settings.compat
    est = estimate_tokens({"m": messages, "t": tools or [], "s": schema or {}}) + max_tokens
    if c.tpm and est > c.tpm:
        raise TooLarge(est)
    if ctx.state.get("model_announced") is None:
        ctx.state["model_announced"] = model
        ctx.emit("run.model", model=model, provider=c.label)
    kwargs: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
    if tools:
        kwargs.update(tools=tools, tool_choice="auto")
    if schema:
        kwargs["response_format"] = {"type": "json_schema",
                                     "json_schema": {"name": "result", "schema": schema, "strict": True}}
    elif json_object:
        kwargs["response_format"] = {"type": "json_object"}
    extra_kwargs, extra_body = _reasoning_params(c, model, effort)
    kwargs.update(extra_kwargs)
    for attempt in range(6):
        ctx.check()
        entry = await _budget.take(ctx, model, est, c.tpm, c.label)
        try:
            resp = await ctx.client.chat.completions.create(**kwargs, extra_body=extra_body or None)
        except openai.RateLimitError as e:
            msg = str(getattr(e, "message", e))
            if "per day" in msg.lower() or "(rpd)" in msg.lower() or "(tpd)" in msg.lower():
                raise AgentStopped(f"{c.label}'s daily free limit for {model} is used up. Try again tomorrow, or "
                                   f"point GROQ_AGENT_MODEL / GROQ_ANALYST_MODELS at other models.")
            retry_after = 0.0
            try:
                retry_after = float(e.response.headers.get("retry-after") or 0)
            except (TypeError, ValueError, AttributeError):
                pass
            delay = min(60.0, retry_after or 6.0 * 2 ** attempt)
            if attempt == 0:
                ctx.emit("agent.note", kind="system", text=f"{c.label} rate limit hit; retrying in {delay:.0f}s.")
            await asyncio.sleep(delay)
            continue
        except openai.APIStatusError as e:
            text = str(getattr(e, "message", e)).lower()
            if e.status_code == 413 or "too large" in text or "reduce the length" in text:
                raise TooLarge(est) from e
            if e.status_code == 400 and any(k in text for k in REJECTED_TOOL_CALL):
                raise ToolCallRejected(str(getattr(e, "message", e))[:400]) from e
            if e.status_code >= 500 and attempt < 4:
                await asyncio.sleep(4.0 * (attempt + 1))
                continue
            raise
        if entry is not None and resp.usage:
            entry[1] = resp.usage.total_tokens or entry[1]
        u = resp.usage
        if u:
            ctx.usage["input_tokens"] += u.prompt_tokens or 0
            ctx.usage["output_tokens"] += u.completion_tokens or 0
        ctx.usage["requests"] += 1
        ctx.emit("usage", **ctx.usage, cost_usd=None)
        return resp
    raise AgentStopped(f"{c.label} kept rate-limiting this request; wait a minute and retry.")


def _reasoning_text(msg: Any) -> str:
    r = getattr(msg, "reasoning", None)
    if r is None and getattr(msg, "model_extra", None):
        r = msg.model_extra.get("reasoning")
    return (r or "").strip() if isinstance(r, str) else ""


def _short(text: str, n: int = 400) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 20].rsplit(" ", 1)[0] + "…"


# ── agent loop ────────────────────────────────────────────────────────────


def _lenient(node: Any) -> Any:
    """Drop "required": some providers (Groq) reject a whole response server-side when a tool call
    misses a field. Handover validates every call itself and answers with a precise error instead."""
    if isinstance(node, dict):
        return {k: _lenient(v) for k, v in node.items() if k != "required"}
    if isinstance(node, list):
        return [_lenient(v) for v in node]
    return node


def _tool_defs(tools: list[ToolSpec], lenient: bool = False) -> list[dict]:
    from .gemini import _inline_refs   # same $ref inlining keeps schemas portable

    out = []
    for t in tools:
        schema = _inline_refs(_strip_titles(t.model.model_json_schema()))
        if lenient:
            required = schema.get("required", [])
            schema = _lenient(schema)
            if required:
                desc = t.description + f" Required: {', '.join(required)}."
                out.append({"type": "function", "function": {"name": t.name, "description": desc, "parameters": schema}})
                continue
        out.append({"type": "function", "function": {"name": t.name, "description": t.description, "parameters": schema}})
    return out


SMALL_OUTPUT_NOTE = ("\n\nOutput budget: each reply is limited to about 800 tokens. Make at most 4 tool calls per "
                     "reply, keep any text to one short sentence, and spread bigger batches over several replies.")


def _trim(messages: list, budget: int) -> None:
    """Keep the conversation under budget: elide old tool results first, then long
    assistant notes, then (last resort) shorten the newest tool results."""
    if budget <= 0 or estimate_tokens(messages) <= budget:
        return
    last_round = max((i for i, m in enumerate(messages) if m["role"] == "assistant" and m.get("tool_calls")), default=0)
    for i, m in enumerate(messages[2:last_round], start=2):
        if estimate_tokens(messages) <= budget:
            return
        if m["role"] == "tool" and not str(m["content"]).startswith("[elided"):
            m["content"] = "[elided to save tokens] " + str(m["content"])[:160]
        elif m["role"] == "assistant" and isinstance(m.get("content"), str) and len(m["content"]) > 240:
            m["content"] = m["content"][:240] + "…"
    for m in sorted((m for m in messages[last_round:] if m["role"] == "tool"), key=lambda m: -len(str(m["content"]))):
        over = estimate_tokens(messages) - budget
        if over <= 0:
            return
        body = str(m["content"])
        keep = max(400, len(body) - int(over * 3.4) - 200)
        m["content"] = body[:keep] + " … [truncated to fit the per-minute token budget]"


async def run_agent(ctx: RunContext, *, system: str, user: str | list, tools: list[ToolSpec], max_turns: int = 40,
                    max_tokens: int = 64000, effort: str | None = None, stream_text: bool = False,
                    history: list[dict] | None = None) -> str:
    c: CompatConfig = ctx.settings.compat
    model = c.agent_model
    by_name = {t.name: t for t in tools}
    defs = _tool_defs(tools, lenient=c.groq)
    # free tiers cap output tokens per minute too (Groq: ~1,000 for Qwen), so keep each turn small
    out_tokens = 1200 if c.tpm else 8000
    user_text = user if isinstance(user, str) else "\n".join(b.get("text", "") for b in user if b.get("type") == "text")
    messages: list[dict] = [{"role": "system", "content": system + (SMALL_OUTPUT_NOTE if c.tpm else "")},
                            *({"role": h["role"], "content": h["content"]} for h in history or []),
                            {"role": "user", "content": user_text}]
    # Qwen on Groq: "default" = no reasoning tokens, so the whole output budget goes to the tool calls
    agent_effort = ("default" if "qwen" in model else "low") if c.groq else ""
    final, retries, rejected = "", 0, 0
    base_system = messages[0]["content"]
    pinned = ctx.state.get("pinned_context")
    for _ in range(max_turns):
        ctx.check()
        if callable(pinned):
            # facts the model must never lose to trimming (e.g. document ids), refreshed every turn
            extra = pinned()
            messages[0]["content"] = base_system + (f"\n\n{extra}" if extra else "")
        if c.tpm:
            _trim(messages, c.tpm - out_tokens - estimate_tokens(defs) - 150)
        try:
            resp = await _chat(ctx, model, messages, tools=defs, max_tokens=out_tokens, effort=agent_effort)
        except TooLarge:
            retries += 1
            if retries > 2:
                raise AgentStopped("The conversation outgrew the provider's per-request token limit.")
            _trim(messages, max(1500, (c.tpm or 8000) // 2))
            continue
        except ToolCallRejected as e:
            # tell the model exactly what was wrong; it nearly always fixes the call on the next try
            rejected += 1
            if rejected > 3:
                raise AgentStopped(f"{c.label} kept rejecting the model's tool calls: {e}")
            reason = re.sub(r"^.*?(tool call validation failed:?|tool_use_failed:?)\s*", "", str(e), flags=re.I)
            ctx.emit("agent.note", kind="system",
                     text=f"{c.label} rejected a malformed tool call ({_short(reason, 160)}); asking the model to fix it.")
            messages.append({"role": "user", "content": f"Your last tool call was rejected: {e}. Call the tool "
                                                        f"again with every required parameter, or answer without "
                                                        f"tools if you are done."})
            continue
        choice = resp.choices[0]
        msg = choice.message
        reasoning = _reasoning_text(msg)
        if reasoning:
            ctx.emit("agent.note", kind="update", text=_short(reasoning))
        content = THINK.sub("", msg.content or "").strip()
        calls = msg.tool_calls or []
        if not calls:
            final = content or final
            if stream_text and final:
                for i in range(0, len(final), 48):
                    ctx.emit("agent.text_delta", text=final[i:i + 48])
                    await asyncio.sleep(0.01)
            elif final:
                ctx.emit("agent.note", kind="text", text=final)
            return final
        if content:
            ctx.emit("agent.note", kind="text", text=content)
        messages.append({"role": "assistant", "content": content or None, "tool_calls": [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}} for tc in calls]})
        if choice.finish_reason == "length":
            results = [{"content": "This tool call was cut off by the output limit; send fewer or shorter calls.",
                        "is_error": True} for _ in calls]
        else:
            parsed = []
            for tc in calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except ValueError:
                    args = None
                parsed.append(args if isinstance(args, dict) else {"__invalid_json__": tc.function.arguments})
            results = list(await asyncio.gather(*(invoke_tool(ctx, by_name.get(tc.function.name), a, tc.id,
                                                              tc.function.name) for tc, a in zip(calls, parsed))))
        for tc, r in zip(calls, results):
            body = r["content"] if isinstance(r["content"], str) else json.dumps(r["content"], ensure_ascii=False)
            m = {"role": "tool", "tool_call_id": tc.id, "content": ("ERROR: " + body) if r.get("is_error") else body}
            if c.groq:
                m["name"] = tc.function.name
            messages.append(m)
    raise AgentStopped(f"The agent hit its {max_turns}-turn limit before finishing.")


# ── documents and structured calls ────────────────────────────────────────


def _pdf_pages(data: bytes) -> tuple[str, list[tuple[bytes, str]]]:
    """Text layer, plus page images for pages that have no usable text (scans)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    texts, images = [], []
    for page in reader.pages:
        t = (page.extract_text() or "").strip()
        texts.append(t)
        if len(t) < 40:
            for img in page.images[:1]:
                try:
                    pil = img.image.convert("RGB")
                    pil.thumbnail((1600, 1600))
                    buf = io.BytesIO()
                    pil.save(buf, "JPEG", quality=80)
                    images.append((buf.getvalue(), "image/jpeg"))
                except Exception:
                    continue
    return "\n\n".join(t for t in texts if t), images


def _flatten(content: list) -> tuple[str, list[tuple[bytes, str]], str]:
    parts, images, title = [], [], ""
    for b in content:
        kind = b.get("type")
        if kind == "text":
            parts.append(b["text"])
        elif kind in ("document", "image"):
            src = b.get("source", {})
            title = b.get("title") or title
            if src.get("type") == "text":
                parts.append(f"<document title=\"{title}\">\n{src.get('data', '')}\n</document>")
            elif src.get("type") == "base64":
                raw = base64.b64decode(src["data"])
                if src.get("media_type") == "application/pdf":
                    text, imgs = _pdf_pages(raw)
                    images += imgs
                    if text:
                        parts.append(f"<document title=\"{title}\">\n{text}\n</document>")
                else:
                    images.append((raw, src.get("media_type", "image/jpeg")))
    return "\n\n".join(parts), images, title


async def _transcribe(ctx: RunContext, data: bytes, mime: str, page: int, pages: int, title: str) -> str:
    c: CompatConfig = ctx.settings.compat
    key = hashlib.sha1(data).hexdigest()
    if key in _transcripts:
        return _transcripts[key]
    url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    messages = [{"role": "user", "content": [
        {"type": "text", "text": f"This is page {page} of {pages} of a scanned document ({title}). Transcribe all "
                                 f"text exactly, in its original language, keeping headings and line breaks. Mention "
                                 f"signatures, stamps or seals in [brackets]. Output only the transcription."},
        {"type": "image_url", "image_url": {"url": url}}]}]
    resp = await _chat(ctx, c.vision_model, messages, max_tokens=1800, effort="default")
    text = THINK.sub("", resp.choices[0].message.content or "").strip()
    _transcripts[key] = text
    return text


async def structured_call(ctx: RunContext, *, system: str, content: list, schema: dict, max_tokens: int = 24000,
                          effort: str | None = None) -> dict:
    c: CompatConfig = ctx.settings.compat
    text, images, title = _flatten(content)
    if images:
        if not c.vision_model:
            text += "\n\n[This document has scanned pages, but no vision model is configured to read them.]"
        else:
            pages = []
            for i, (data, mime) in enumerate(images[:6], start=1):
                pages.append(await _transcribe(ctx, data, mime, i, len(images[:6]), title or "document"))
            text += "\n\n" + "\n\n".join(f"<scanned_page number=\"{i}\">\n{p}\n</scanned_page>"
                                         for i, p in enumerate(pages, start=1))
    model = _next_analyst(c)
    out_tokens = 3500 if c.tpm else 12000
    use_schema = True
    for _ in range(4):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": text}]
        try:
            if use_schema:
                resp = await _chat(ctx, model, messages, schema=schema, max_tokens=out_tokens)
            else:   # endpoint without json_schema support: JSON mode + the schema in the prompt
                messages[1]["content"] = (text + "\n\nReturn only a JSON object that matches this JSON Schema:\n"
                                          + json.dumps(schema, separators=(",", ":")))
                resp = await _chat(ctx, model, messages, json_object=True, max_tokens=out_tokens)
        except TooLarge:
            text = text[: int(len(text) * 0.7)] + "\n[document shortened to fit the per-minute token budget]"
            continue
        except openai.BadRequestError as e:
            if use_schema and ("json_schema" in str(e).lower() or "response_format" in str(e).lower()):
                use_schema = False
                continue
            raise
        raw = THINK.sub("", resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
        try:
            return json.loads(raw)
        except ValueError:
            if resp.choices[0].finish_reason == "length":
                out_tokens = min(out_tokens + 1500, (c.tpm - 1500) if c.tpm else 16000)
            continue
    raise RuntimeError(f"{c.label} did not return valid JSON for this document.")


def error_text(e: BaseException, label: str) -> str | None:
    if isinstance(e, openai.AuthenticationError):
        return f"{label} rejected the API key. Check it in .env (or switch to Simulation mode)."
    if isinstance(e, openai.NotFoundError):
        return f"{label} doesn't know one of the configured models: {getattr(e, 'message', e)}"
    if isinstance(e, openai.RateLimitError):
        return f"{label} rate limit reached; wait a minute and retry."
    if isinstance(e, openai.APIConnectionError):
        return f"Could not reach {label}; check the network connection."
    if isinstance(e, openai.APIStatusError):
        return f"{label} API error {e.status_code}: {getattr(e, 'message', e)}"
    return None
