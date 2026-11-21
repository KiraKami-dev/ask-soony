"""
Small API for the decision inbox UI.

Reads the verifier's decision cards, stores human decisions, the autonomy mode and an event feed
under data/state/. Nothing about the customer is hardcoded here: the state file is passed through.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "web" / "fixtures" / "sample_state.json"
DIST = ROOT / "web" / "dist"

app = FastAPI(title="ask-soony dashboard")


def _cards_path() -> Path:
    default = ROOT / "data" / "store" / "decision_cards.json"
    return Path(os.environ.get("ASK_SOONY_STATE") or default)


def _state_dir() -> Path:
    return Path(os.environ.get("ASK_SOONY_STATE_DIR") or ROOT / "data" / "state")


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _add_event(agent: str, text: str) -> None:
    path = _state_dir() / "feed.json"
    events = _read(path, [])
    events.append({"ts": _now(), "agent": agent, "text": text})
    _write(path, events)


@app.get("/api/state")
def get_state():
    path = _cards_path()
    if path.exists():
        state, source = _read(path, {}), "verifier"
    else:
        state, source = _read(FIXTURE, {}), "fixture"
    state.pop("feed", None)
    state["source"] = source
    decisions = _read(_state_dir() / "decisions.json", {})
    for card in state.get("cards", []):
        mine = decisions.get(card.get("id"))
        if mine:
            card["human_decision"] = mine
    state["mode"] = _read(_state_dir() / "mode.json", {}).get("mode", "ask")
    return state


class Action(BaseModel):
    action: Literal["allow_once", "allow_always", "edit", "dismiss"]
    note: str | None = None


@app.post("/api/cards/{card_id}/action")
def post_action(card_id: str, body: Action):
    path = _state_dir() / "decisions.json"
    decisions = _read(path, {})
    decisions[card_id] = {"action": body.action, "note": body.note, "ts": _now()}
    _write(path, decisions)
    _add_event("human", f"{body.action} on {card_id}")
    return decisions[card_id]


@app.get("/api/feed")
def get_feed():
    events = _read(_state_dir() / "feed.json", [])
    if not events:
        events = _read(FIXTURE, {}).get("feed", [])
    return sorted(events, key=lambda e: e.get("ts", ""), reverse=True)


class Mode(BaseModel):
    mode: Literal["ask", "autonomous"]


@app.get("/api/mode")
def get_mode():
    return {"mode": _read(_state_dir() / "mode.json", {}).get("mode", "ask")}


@app.post("/api/mode")
def set_mode(body: Mode):
    _write(_state_dir() / "mode.json", {"mode": body.mode})
    _add_event("human", f"mode set to {body.mode}")
    return {"mode": body.mode}


if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        file = DIST / path
        return FileResponse(file if path and file.is_file() else DIST / "index.html")
