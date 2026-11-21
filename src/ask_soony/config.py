"""
Settings from environment variables, with an optional .env file in the working directory.

Nothing about the customer's data lives here: only where to find the portal, which model to
use and where to send notifications.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE lines into os.environ without overriding variables that are already set."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        # Empty template lines must not shadow credentials the SDKs find elsewhere.
        if value:
            os.environ.setdefault(key.strip(), value)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    portal_url: str
    portal_token: str
    data_dir: Path
    # LLM: "anthropic", "openai" (OpenAI or any OpenAI-compatible server such as llama.cpp)
    # or "none" (poll and store only).
    llm_provider: str
    llm_model: str
    llm_base_url: str
    llm_api_key: str
    llm_effort: str
    interval_seconds: int
    notifiers: list[str] = field(default_factory=list)
    discord_webhook_url: str = ""
    webhook_url: str = ""
    # Findings at or above this severity are pushed to the notifiers.
    notify_min_severity: str = "high"
    dashboard_password: str = ""
    dashboard_port: int = 8765

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        provider = _env("ASK_LLM_PROVIDER", "anthropic").lower()
        default_model = {"anthropic": "claude-opus-5-5", "openai": "gpt-5"}.get(provider, "")
        notifiers = [n.strip() for n in _env("ASK_NOTIFIERS", "console").split(",") if n.strip()]
        return cls(
            portal_url=_env("SOONY_PORTAL_URL").rstrip("/"),
            portal_token=_env("SOONY_PORTAL_TOKEN"),
            data_dir=Path(_env("ASK_DATA_DIR", "data/store")),
            llm_provider=provider,
            llm_model=_env("ASK_LLM_MODEL", default_model),
            llm_base_url=_env("ASK_LLM_BASE_URL"),
            llm_api_key=_env("ASK_LLM_API_KEY"),
            llm_effort=_env("ASK_LLM_EFFORT", "medium"),
            interval_seconds=int(_env("ASK_INTERVAL_SECONDS", "300")),
            notifiers=notifiers,
            discord_webhook_url=_env("ASK_DISCORD_WEBHOOK_URL"),
            webhook_url=_env("ASK_WEBHOOK_URL"),
            notify_min_severity=_env("ASK_NOTIFY_MIN_SEVERITY", "high").lower(),
            dashboard_password=_env("ASK_DASHBOARD_PASSWORD"),
            dashboard_port=int(_env("ASK_DASHBOARD_PORT", "8765")),
        )
