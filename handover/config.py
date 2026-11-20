"""Settings, re-read from .env on every access so a key pasted while the
server runs takes effect on the next agent run."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
DATA_DIR = ROOT / "data"
SAMPLE_DIR = ROOT / "sample_data"
DEMO_DRIVE = SAMPLE_DIR / "demo_drive"
GROUND_TRUTH = SAMPLE_DIR / "ground_truth.json"
FRONTEND_DIST = ROOT / "frontend" / "dist"

for sub in ("", "drafts", "outbox", "drive_filings"):
    (DATA_DIR / sub).mkdir(parents=True, exist_ok=True)

# Opus 5.5 list prices, USD per million tokens (input, output, cache write 5m, cache read)
PRICES = {"input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.20}


@dataclass(frozen=True)
class CompatConfig:
    """An OpenAI-compatible endpoint (Groq, OpenRouter, Cerebras, Ollama, ...)."""
    label: str
    base_url: str
    api_key: str
    agent_model: str               # orchestrator: needs tool calling (parallel calls preferred)
    analyst_models: tuple[str, ...]  # document analysis / drafting (round-robin spreads per-model limits)
    vision_model: str              # reads scanned pages; "" = text only
    tpm: int                       # client-side tokens-per-minute budget per model; 0 = unlimited
    groq: bool                     # send Groq-specific reasoning parameters


@dataclass(frozen=True)
class Settings:
    api_key: str
    auth_token: str
    gemini_api_key: str
    compat: CompatConfig | None
    provider: str             # "anthropic" | "gemini" | "groq" | "openai_compat" | "none"
    mode: str                 # "live" | "simulate"
    mode_setting: str         # "auto" | "live" | "simulate"
    model: str                # Claude model id, or the Gemini model ("auto" = best available Flash)
    effort: str
    analyst_concurrency: int
    company_name: str
    counsel_name: str
    counsel_email: str
    hr_email: str
    today_override: date | None
    drive_source: str
    google_client_file: Path
    google_root_folder: str
    mail_transport: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str
    mail_redirect_all_to: str

    @property
    def has_credentials(self) -> bool:
        return self.provider != "none"

    @property
    def provider_label(self) -> str:
        if self.provider in ("groq", "openai_compat") and self.compat:
            return self.compat.label
        return {"anthropic": "Claude", "gemini": "Gemini"}.get(self.provider, "none")


_cache: tuple[float, Settings] | None = None


def _values() -> dict[str, str]:
    file_vals = {k: (v or "") for k, v in dotenv_values(ENV_FILE).items()} if ENV_FILE.exists() else {}
    merged = dict(file_vals)
    for k, v in os.environ.items():   # real environment variables win when set
        if v.strip() and (k in file_vals or k.startswith(("ANTHROPIC_", "HANDOVER_", "SMTP_", "MAIL_", "GEMINI_",
                                                          "GROQ_", "OPENAI_COMPAT_"))
                          or k in ("GOOGLE_API_KEY", "LLM_PROVIDER")):
            merged[k] = v
    return {k: v.strip() for k, v in merged.items()}


def get_settings() -> Settings:
    global _cache
    mtime = ENV_FILE.stat().st_mtime if ENV_FILE.exists() else 0.0
    if _cache and _cache[0] == mtime:
        return _cache[1]
    v = _values()
    api_key = v.get("ANTHROPIC_API_KEY", "")
    auth_token = v.get("ANTHROPIC_AUTH_TOKEN", "")
    gemini_key = v.get("GEMINI_API_KEY") or v.get("GOOGLE_API_KEY") or ""
    groq = CompatConfig(
        label="Groq", base_url="https://api.groq.com/openai/v1", api_key=v.get("GROQ_API_KEY", ""),
        agent_model=v.get("GROQ_AGENT_MODEL") or "qwen/qwen3.8-27b",
        analyst_models=tuple(m.strip() for m in (v.get("GROQ_ANALYST_MODELS")
                                                 or "openai/gpt-oss-120b,openai/gpt-oss-20b").split(",") if m.strip()),
        vision_model=v.get("GROQ_VISION_MODEL") or "qwen/qwen3.8-27b",
        tpm=int(v.get("GROQ_TPM") or 8000), groq=True)
    custom = CompatConfig(
        label=v.get("OPENAI_COMPAT_LABEL") or "OpenAI-compatible", base_url=v.get("OPENAI_COMPAT_BASE_URL", ""),
        api_key=v.get("OPENAI_COMPAT_API_KEY") or "not-needed",
        agent_model=v.get("OPENAI_COMPAT_MODEL", ""),
        analyst_models=tuple(m.strip() for m in (v.get("OPENAI_COMPAT_ANALYST_MODELS")
                                                 or v.get("OPENAI_COMPAT_MODEL", "")).split(",") if m.strip()),
        vision_model=v.get("OPENAI_COMPAT_VISION_MODEL", ""), tpm=int(v.get("OPENAI_COMPAT_TPM") or 0), groq=False)
    available = {
        "anthropic": bool(api_key or auth_token),
        "groq": bool(groq.api_key),
        "gemini": bool(gemini_key),
        "openai_compat": bool(custom.base_url and custom.agent_model),
    }
    wanted = (v.get("LLM_PROVIDER") or "auto").lower().replace("claude", "anthropic")
    if wanted in available:
        provider = wanted if available[wanted] else "none"
    else:  # auto: first provider with a credential, in this order
        provider = next((p for p in ("anthropic", "groq", "gemini", "openai_compat") if available[p]), "none")
    compat = groq if provider == "groq" else (custom if provider == "openai_compat" else None)
    mode_setting = (v.get("HANDOVER_MODE") or "auto").lower()
    if mode_setting == "simulate" or provider == "none":
        mode = "simulate"
    else:
        mode = "live"
    today_raw = v.get("HANDOVER_TODAY", "")
    try:
        today_override = date.fromisoformat(today_raw) if today_raw else None
    except ValueError:
        today_override = None
    s = Settings(
        api_key=api_key,
        auth_token=auth_token,
        gemini_api_key=gemini_key,
        compat=compat,
        provider=provider,
        mode=mode,
        mode_setting=mode_setting,
        model=(v.get("GEMINI_MODEL") or "auto") if provider == "gemini"
        else (compat.agent_model if compat else (v.get("HANDOVER_MODEL") or "claude-opus-5-5")),
        effort=(v.get("HANDOVER_EFFORT") or "medium").lower(),
        analyst_concurrency=max(1, int(v.get("ANALYST_CONCURRENCY") or 4)),
        company_name=v.get("COMPANY_NAME") or "Aster Robotics",
        counsel_name=v.get("COUNSEL_NAME") or "Alex Rivera",
        counsel_email=v.get("COUNSEL_EMAIL") or "counsel@aster-robotics.example",
        hr_email=v.get("HR_EMAIL") or "people@aster-robotics.example",
        today_override=today_override,
        drive_source=(v.get("DRIVE_SOURCE") or "local").lower(),
        google_client_file=ROOT / (v.get("GOOGLE_OAUTH_CLIENT_FILE") or "credentials/google_oauth_client.json"),
        google_root_folder=v.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", ""),
        mail_transport=(v.get("MAIL_TRANSPORT") or "sandbox").lower(),
        smtp_host=v.get("SMTP_HOST", ""),
        smtp_port=int(v.get("SMTP_PORT") or 587),
        smtp_username=v.get("SMTP_USERNAME", ""),
        smtp_password=v.get("SMTP_PASSWORD", ""),
        smtp_from=v.get("SMTP_FROM", ""),
        mail_redirect_all_to=v.get("MAIL_REDIRECT_ALL_TO", ""),
    )
    _cache = (mtime, s)
    return s


def today() -> date:
    """The app's notion of 'today': HANDOVER_TODAY, else the real date."""
    return get_settings().today_override or date.today()
