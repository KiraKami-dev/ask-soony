"""
Small API for the decision inbox UI.

Reads the verifier's decision cards, stores human decisions, the autonomy mode and an event feed
under data/state/. Nothing about the customer is hardcoded here: the state file is passed through.
"""

import glob
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ask_soony import feed
from ask_soony.network import build_network

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "web" / "fixtures" / "sample_state.json"
NETWORK_FIXTURE = ROOT / "web" / "fixtures" / "sample_network.json"
FIXTURES = [FIXTURE, ROOT / "web" / "fixtures" / "sample_forecast_cards.json"]
DIST = ROOT / "web" / "dist"

log = logging.getLogger("uvicorn.error")
app = FastAPI(title="ask-soony dashboard")


def _store_dir() -> Path:
    return Path(os.environ.get("ASK_DATA_DIR") or ROOT / "data" / "store")


def _card_files() -> list[Path]:
    """ASK_SOONY_STATE (paths, globs, comma-separated) or every *cards*.json in the store."""
    spec = os.environ.get("ASK_SOONY_STATE")
    if spec:
        found: list[Path] = []
        for part in filter(None, (p.strip() for p in spec.split(","))):
            found += [Path(m) for m in sorted(glob.glob(part))]
        return [p for p in found if p.is_file()]
    return sorted(_store_dir().glob("*cards*.json"))


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


def _add_event(actor: str, text: str, ref: str | None = None) -> None:
    path = _state_dir() / "feed.json"
    events = _read(path, [])
    event = {"at": _now(), "actor": actor, "text": text}
    if ref:
        event["ref"] = ref
    events.append(event)
    _write(path, events)


def _load_state(files: list[Path]) -> dict:
    """Merge card files: cards by id (later wins), other fields from the first file with them."""
    state: dict = {"editions": [], "parts": [], "cards": []}
    cards: dict[str, dict] = {}
    for f in files:
        data = _read(f, {})
        if not isinstance(data, dict):
            continue
        for key in ("editions", "parts", "now", "hours_left", "packing_ends"):
            if data.get(key) and not state.get(key):
                state[key] = data[key]
        for card in data.get("cards", []):
            cid = card.get("id")
            if cid in cards:
                log.warning(
                    "duplicate card id %s: %s overrides %s", cid, f.name, cards[cid]["source_file"]
                )
            cards[cid] = {**card, "source_file": f.name, "module": f.stem}
    state["cards"] = list(cards.values())
    return state


@app.get("/api/state")
def get_state():
    files = _card_files()
    if files:
        state, source = _load_state(files), "files"
    else:
        state, source = _load_state([p for p in FIXTURES if p.exists()]), "fixture"
    state["source"] = source
    state["sources"] = [f.name for f in files or FIXTURES]
    decisions = _read(_state_dir() / "decisions.json", {})
    status = _action_status()
    for card in state["cards"]:
        if card["id"] in decisions:
            card["human_decision"] = decisions[card["id"]]
        if card["id"] in status:
            card["action_status"] = status[card["id"]]
    state["mode"] = _read(_state_dir() / "mode.json", {}).get("mode", "ask")
    return state


def _action_status() -> dict[str, dict]:
    """data/state/actions.json: {card_id: {...}} or a list of {card_id, status, at, reply?}."""
    raw = _read(_state_dir() / "actions.json", {})
    if isinstance(raw, list):
        raw = {r["card_id"]: r for r in raw if isinstance(r, dict) and "card_id" in r}
    return raw if isinstance(raw, dict) else {}


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
    events = []
    for e in _read(_state_dir() / "feed.json", []):  # older files used ts/agent
        events.append(
            {
                **e,
                "at": e.get("at") or e.get("ts", ""),
                "actor": e.get("actor") or e.get("agent", ""),
            }
        )
    store = _store_dir()
    events += feed.db_events(store / "ask-soony.db") + feed.mail_events(store.parent)
    if not events:
        events = _read(FIXTURE, {}).get("feed", [])
    return sorted(events, key=lambda e: e.get("at", ""), reverse=True)


@app.get("/api/network")
def get_network():
    root = Path(os.environ.get("ASK_PORTAL_DIR") or ROOT / "data" / "soony-data")
    net = build_network(root)
    source = "portal"
    if net is None:
        net, source = _read(NETWORK_FIXTURE, {}), "fixture"
    net["source"] = source
    net["stale_hours"] = float(os.environ.get("ASK_STALE_HOURS") or 24)
    return net


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
