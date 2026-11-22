"""
Routines: after a person decided something by hand, an agent offers to do it automatically next
time. It writes a small Python routine from a fixed set of building blocks, which runs in the
Monty sandbox (pydantic-monty) on every check: no network, no files, no imports except the
`soony` module below, type-checked against its stubs before it is saved.

The trust ladder stays: a routine can carry out an option only if it just communicates (an email
or a message to a person) and costs nothing. Re-plans, allocations, orders and anything with a
price still go to a person; the routine can only notify about them. Every run
is recorded with what it printed and what it did, and every action it takes lands in the inbox
history like a click, marked with the routine's name.

Building blocks (module `soony`):
  open_cards() -> list[dict]         open decisions: id, headline, level, kind, part, supplier,
                                     number, deadline, evidence[{source, value}], options[...]
  carry_out(card_id, option_index, reason) -> dict   approve an option (sends its email if any)
  notify(text, card_id) -> None      push and Teams
  log(text) -> None                  a line in the run record
  first_time(key) -> bool            True once per key per routine (avoid repeating yourself)
  now() -> str                       current time, ISO 8601
"""

import json
import logging
import os
import re
import threading
import time
import uuid

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ask_soony.config import load_dotenv

log = logging.getLogger("ask_soony.routines")
router = APIRouter()
_lock = threading.Lock()

STUBS = '''
def open_cards() -> list[dict]:
    """Open decisions. Keys: id, headline, level ('red'|'amber'|'grey'|'blue'), kind, part,
    supplier_id, supplier_name, number (copies at risk or units short, may be None), deadline,
    evidence (list of {'source': str, 'value': str}), options (list of {'index': int,
    'action': str, 'cost': str | None, 'free': bool, 'channel': str | None, 'to': str | None,
    'routine_type': str | None}). `free` is True only for an option that just sends a message
    (email or note) and costs nothing; re-plans, allocations and orders are never free."""
    ...

def carry_out(card_id: str, option_index: int, reason: str) -> dict:
    """Approve one option of a card, exactly like the Approve button (an email option to a known
    supplier is sent). Refused for options that cost money: notify a person instead.
    Returns {'ok': bool, 'status': str, 'error': str | None}."""
    ...

def notify(text: str, card_id: str) -> None:
    """Tell the buyer (push to their phone, Teams channel)."""
    ...

def log(text: str) -> None:
    """Write one line to this run's record."""
    ...

def first_time(key: str) -> bool:
    """True the first time this routine sees `key`, False afterwards."""
    ...

def now() -> str:
    """Current time, ISO 8601."""
    ...
'''

WRITER_PROMPT = """You write small automation routines for a procurement decision inbox.
A buyer just decided a card by hand. Write a routine that handles SIMILAR future cards the same
way, so they do not have to do it again. Generalise from this card: match on the situation
(wording of the evidence and the option, its channel and recipient role, kind), never on this
card's id, part or supplier unless the buyer's decision clearly depends on them. Do not match on
level: it is only the current urgency colour and changes over time.

The routine is plain Python run in a sandbox. It may only `from soony import ...` these
functions (their stubs):
{stubs}

Rules:
- Top-level code, no def main(), no other imports, no network, no files, no classes.
- Loop over open_cards(); pick the matching ones; use first_time(f"...{{card['id']}}") so a
  card is handled once.
- Call carry_out only for an option with free == True. Otherwise
  notify the buyer with one short sentence and do not carry it out.
- log() one short line per card you handle. Keep it under 30 lines.
- The value of the last expression is the run's result: end with the number of cards handled.

The card the buyer decided:
{card}

What the buyer did: {action}

Answer with JSON only: {{"name": "<4-7 words>", "description": "<one or two plain sentences a
buyer understands: when it triggers and what it does>", "code": "<the routine>"}}

Name and description are read by a buyer, not a developer: describe the business situation and
the action ("When a container sits at the port ready for pick-up, tell the DC site lead to
collect it"). Never use internal field names or codes such as amber, red, level, kind, mismatch,
routine_type, channel, card or option index."""


# ---------------------------------------------------------------- storage


def _all() -> list[dict]:
    from ask_soony import api

    return api.read_list("routines.json")


def _save(items: list[dict]) -> None:
    from ask_soony import api

    api.write_json("routines.json", items)


def _get(rid: str) -> dict:
    r = next((x for x in _all() if x.get("id") == rid), None)
    if r is None:
        raise HTTPException(404, "unknown routine")
    return r


# ---------------------------------------------------------------- building blocks


def _card_view(c: dict) -> dict:
    return {
        "id": c["id"],
        "headline": c.get("headline"),
        "level": c.get("level"),
        "kind": c.get("kind"),
        "part": c.get("part"),
        "supplier_id": c.get("supplier_id"),
        "supplier_name": c.get("supplier_name"),
        "number": c.get("number"),
        "deadline": c.get("deadline"),
        "evidence": [
            {
                "source": str(e.get("source") or ""),
                "value": str(e.get("value") or e.get("quote") or ""),
            }
            for e in c.get("evidence") or []
        ],
        "options": [
            {
                "index": o.get("index", i),
                "action": o.get("action"),
                "cost": o.get("cost"),
                "free": automatable(o),
                "channel": o.get("channel"),
                "to": o.get("to"),
                "routine_type": o.get("routine_type"),
            }
            for i, o in enumerate(c.get("options") or [])
        ],
    }


def _costs_money(option: dict) -> bool:
    cost = str(option.get("cost") or "").strip().lower()
    return bool(cost) and cost not in ("none", "0", "free", "no cost", "-")


# Routines may only carry out options that communicate (an email or a message to a person).
# Re-plans, allocations and orders change the plan or commit money: never automatic, whatever
# their cost text says.
AUTOMATABLE_CHANNELS = {"email", "notify"}


def automatable(option: dict) -> bool:
    return option.get("channel") in AUTOMATABLE_CHANNELS and not _costs_money(option)


class _Run:
    """The building blocks for one run; in dry mode side effects are only recorded."""

    def __init__(self, routine: dict, dry: bool):
        self.routine, self.dry = routine, dry
        self.lines: list[str] = []
        self.did: list[dict] = []
        from ask_soony import api

        state = api.build_state()
        self.cards = [
            c for c in state["cards"] if c["id"] in state["inbox_ids"] and c["status"] == "open"
        ]
        self.seen = set(routine.get("seen") or [])

    def open_cards(self) -> list[dict]:
        return [_card_view(c) for c in self.cards]

    def carry_out(self, card_id: str, option_index: int, reason: str) -> dict:
        from ask_soony import api

        card = next((c for c in self.cards if c["id"] == card_id), None)
        if card is None:
            return {"ok": False, "status": "unknown", "error": "not an open card"}
        options = card.get("options") or []
        if not 0 <= int(option_index) < len(options):
            return {"ok": False, "status": "open", "error": "no such option"}
        opt = options[int(option_index)]
        if not automatable(opt):
            reason = "costs money" if _costs_money(opt) else "changes the plan"
            return {"ok": False, "status": "open", "error": f"{reason}: a person decides"}
        step = {"do": "carry_out", "card": card_id, "option": opt.get("action"), "reason": reason}
        if self.dry:
            self.did.append({**step, "dry": True})
            return {"ok": True, "status": "would approve", "error": None}
        out = api.approve(
            card_id, {"option": int(option_index), "via": f"routine: {self.routine['name']}"}
        )
        status = out["action"].get("status")
        self.did.append({**step, "status": status})
        return {"ok": True, "status": status, "error": None}

    def notify(self, text: str, card_id: str = "") -> None:
        self.did.append({"do": "notify", "card": card_id, "text": text, "dry": self.dry})
        if self.dry:
            return
        try:
            from ask_soony import push

            push.send(f"⚙️ {self.routine['name']}", text, card_id or None)
        except Exception:
            pass
        try:
            from ask_soony import teams

            if card_id:
                teams.notify_reply(card_id, f"Routine · {self.routine['name']}", text)
        except Exception:
            pass

    def log(self, text: str) -> None:
        self.lines.append(str(text)[:300])

    def first_time(self, key: str) -> bool:
        if key in self.seen:
            return False
        self.seen.add(key)
        return True

    def now(self) -> str:
        from ask_soony import api

        return api.now().isoformat()


def run(routine: dict, dry: bool = False) -> dict:
    """Run one routine in the Monty sandbox; returns the run record."""
    from pydantic_monty import CollectString, ExternalModule, Monty

    ctx = _Run(routine, dry)
    module = ExternalModule(
        {
            "open_cards": ctx.open_cards,
            "carry_out": ctx.carry_out,
            "notify": ctx.notify,
            "log": ctx.log,
            "first_time": ctx.first_time,
            "now": ctx.now,
        },
        stubs=STUBS,
    )
    printed = CollectString(max_bytes=20_000)
    record: dict = {"at": ctx.now(), "dry": dry}
    try:
        with Monty() as pool:
            with pool.checkout(
                external_modules={"soony": module},
                type_check=True,
                limits={"max_feed_duration_secs": 10, "max_memory": 64 * 1024 * 1024},
            ) as session:
                result = session.feed_run(routine["code"], print_callback=printed)
        record.update(ok=True, result=result if isinstance(result, (int, str, float)) else None)
    except Exception as exc:  # syntax, type or runtime errors from the sandbox
        record.update(ok=False, error=str(exc)[:1500])
    output = str(getattr(printed, "output", "") or "")
    record.update(log=ctx.lines + ([output.strip()] if output.strip() else []), did=ctx.did)
    if not dry:
        routine["seen"] = sorted(ctx.seen)
    return record


# ---------------------------------------------------------------- the writer agent


def _complete(prompt: str) -> str:
    """One completion from Claude (ANTHROPIC_API_KEY) or OpenAI (OPENAI_API_KEY)."""
    load_dotenv()
    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic

        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=os.environ.get("ASK_ROUTINE_MODEL", "claude-opus-5-5"),
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if b.type == "text")
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise HTTPException(503, "no model configured: set ANTHROPIC_API_KEY or OPENAI_API_KEY")
    r = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": os.environ.get("ASK_ROUTINE_MODEL", "gpt-4.1"),
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        },
        timeout=90,
    )
    if r.status_code >= 300:
        raise HTTPException(502, f"model refused: {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"]


def _parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0) if m else text)
    code = re.sub(r"^```(?:python)?\s*|\s*```$", "", str(data.get("code") or "").strip())
    return {
        "name": str(data.get("name") or "New routine")[:80],
        "description": str(data.get("description") or "")[:400],
        "code": code,
    }


def suggest(card_id: str) -> dict:
    """Draft a routine from the person's last decision on this card, checked by a dry run."""
    from ask_soony import api

    card = next((c for c in api.build_state()["cards"] if c["id"] == card_id), None)
    if card is None:
        raise HTTPException(404, "unknown card")
    act = next(
        (
            a
            for a in reversed(api.read_list("actions.json"))
            if str(a.get("decision_id")) == card_id
            and a.get("kind") in ("approve", "escalate", "dismiss")
        ),
        None,
    )
    if act is None:
        raise HTTPException(409, "decide this card first, then automate it")
    action = {
        "approve": f"approved option {act.get('option')}: {act.get('action')}"
        f" (status {act.get('status')})",
        "escalate": f"escalated it to {act.get('to')}",
        "dismiss": "dismissed it",
    }[act["kind"]]
    prompt = WRITER_PROMPT.format(
        stubs=STUBS, card=json.dumps(_card_view(card), ensure_ascii=False), action=action
    )
    last_error = ""
    for _ in range(2):  # one repair round if the sandbox rejects the draft
        draft = _parse(
            _complete(
                prompt
                + (
                    f"\n\nYour last draft failed in the sandbox:\n{last_error}\nFix it."
                    if last_error
                    else ""
                )
            )
        )
        draft.update(id=uuid.uuid4().hex[:8], created_from=card_id, enabled=False)
        preview = run(draft, dry=True)
        if preview["ok"]:
            return {"routine": draft, "preview": preview}
        last_error = preview.get("error", "")
    return {"routine": draft, "preview": preview}


# ---------------------------------------------------------------- loop and routes


def run_enabled() -> list[dict]:
    with _lock:
        items = _all()
        records = []
        for r in items:
            if not r.get("enabled"):
                continue
            rec = run(r)
            r["runs"] = ([rec] + (r.get("runs") or []))[:20]
            records.append({"routine": r["id"], **rec})
        _save(items)
    return records


def _loop(interval: int) -> None:
    while True:
        try:
            for rec in run_enabled():
                if rec.get("did") or not rec.get("ok"):
                    log.info("routine %s: %s", rec["routine"], rec.get("did") or rec.get("error"))
        except Exception as exc:
            log.warning("routines failed: %s", exc)
        time.sleep(interval)


def start_background() -> None:
    interval = int(os.environ.get("ASK_ROUTINES_SECONDS", "60"))
    threading.Thread(target=_loop, args=(interval,), daemon=True, name="routines").start()


class Suggest(BaseModel):
    card_id: str


class Draft(BaseModel):
    id: str
    name: str
    description: str
    code: str
    created_from: str | None = None


class Toggle(BaseModel):
    enabled: bool


@router.get("/api/routines")
def list_routines():
    return {"routines": [{k: v for k, v in r.items() if k != "seen"} for r in _all()]}


@router.post("/api/routines/suggest")
def post_suggest(body: Suggest):
    return suggest(body.card_id)


@router.post("/api/routines")
def save_routine(body: Draft):
    from ask_soony import api

    with _lock:
        items = [r for r in _all() if r.get("id") != body.id]
        items.append(
            {
                **body.model_dump(),
                "enabled": True,
                "created_at": api.now().isoformat(),
                "runs": [],
                "seen": [],
            }
        )
        _save(items)
    return {"ok": True}


@router.post("/api/routines/{rid}/toggle")
def toggle_routine(rid: str, body: Toggle):
    with _lock:
        items = _all()
        for r in items:
            if r.get("id") == rid:
                r["enabled"] = body.enabled
        _save(items)
    return _get(rid)


@router.post("/api/routines/{rid}/run")
def run_now(rid: str):
    with _lock:
        items = _all()
        r = next((x for x in items if x.get("id") == rid), None)
        if r is None:
            raise HTTPException(404, "unknown routine")
        rec = run(r)
        r["runs"] = ([rec] + (r.get("runs") or []))[:20]
        _save(items)
    return rec


@router.delete("/api/routines/{rid}")
def delete_routine(rid: str):
    with _lock:
        _save([r for r in _all() if r.get("id") != rid])
    return {"ok": True}
