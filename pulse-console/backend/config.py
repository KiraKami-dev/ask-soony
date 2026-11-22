"""Small, explicit configuration boundary for Console."""

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "console"
# In the integrated ask-soony repository, use this checkout's source directly.
# The environment override still supports a separate development checkout.
SIBLING = Path(os.environ.get("ASK_SOONY_DIR", ROOT if (ROOT / "src" / "ask_soony").is_dir() else ROOT.parent / "ask-soony"))


def load_env() -> None:
    """Load repository settings without overwriting explicitly supplied environment values."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def setting(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def portal_root() -> Path:
    return Path(setting("PULSE_INTELLIGENCE_PORTAL_ROOT") or SIBLING / "data" / "portal")


def readings_root() -> Path:
    return Path(setting("PULSE_INTELLIGENCE_READINGS") or SIBLING / "data" / "store" / "image_recognizer")


def store_root() -> Path:
    return Path(setting("PULSE_INTELLIGENCE_STORE") or SIBLING / "data" / "store")


def state_root() -> Path:
    return Path(setting("PULSE_INTELLIGENCE_STATE") or SIBLING / "data" / "state")
