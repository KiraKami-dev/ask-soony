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

def notify(text: str, card_id: str, channel: str) -> None:
    """Tell the buyer: channel 'phone' (push), 'teams' (Teams channel) or 'both'."""
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

WRITER_PROMPT = """You turn a buyer's decision into an automation routine for a procurement
decision inbox. The buyer just decided a card by hand. Write a routine that handles SIMILAR
future cards the same way, so they do not have to do it again. Generalise from this card: match
on the situation (words in the evidence, the proposed action and its recipient role), never on
this card's id, part or supplier unless the decision clearly depends on them. Do not match on
the urgency colour: it changes over time.

{spec_help}

The card the buyer decided:
{card}

What the buyer did: {action}

Answer with JSON only: {{"name": "<4-7 words>", "description": "<one or two plain sentences a
buyer understands: when it triggers and what it does>", "spec": <the recipe>}}

Name, description and every "text" are read by a buyer, not a developer: describe the business
situation and the action ("When a container sits at the port ready for pick-up, tell the DC
site lead to collect it"). Never use internal field names or codes such as amber, red, level,
kind, mismatch, routine_type, channel, option or card."""

EDIT_PROMPT = """You change an automation routine of a procurement decision inbox exactly as the
buyer asks, and nothing else.

{spec_help}

Current routine:
{routine}

The buyer asks: "{instruction}"

Keep the ids of unchanged conditions and steps; give new ones new ids. If the request cannot be
expressed with the fields above (for example a filter the data does not have), leave the recipe
unchanged and say so in "note". Answer with JSON only: {{"name": "...", "description": "...",
"spec": <the new recipe>, "note": "<empty, or one sentence for the buyer>"}}
Plain buyer language only, no internal field names or codes."""


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

    def notify(self, text: str, card_id: str = "", channel: str = "both") -> None:
        channel = channel if channel in ("phone", "teams", "both") else "both"
        self.did.append(
            {"do": "notify", "card": card_id, "text": text, "channel": channel, "dry": self.dry}
        )
        if self.dry:
            return
        if channel in ("phone", "both"):
            try:
                from ask_soony import push

                push.send(f"Routine: {self.routine['name']}", text, card_id or None)
            except Exception:
                pass
        if channel in ("teams", "both") and card_id:
            try:
                from ask_soony import teams

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


# ---------------------------------------------------------------- recipe (spec) and compiler
#
# The writer agent returns a recipe, not code: conditions ("when") and steps ("then") in plain
# phrases plus a few machine fields. compile_spec() turns it into the sandbox code, so edits by
# text or drag and drop change data only, and the money/plan guard is always compiled in.

FIELDS = {"evidence", "headline", "option_action", "option_to", "supplier_name", "kind"}
OPS = {"contains", "is", "any_of"}
STEPS = {"carry_out", "notify", "log"}
CHANNELS = {"phone", "teams", "both"}


def clean_spec(spec: dict) -> dict:
    """Validate and normalise a recipe; raises ValueError with a buyer-readable reason."""
    if not isinstance(spec, dict):
        raise ValueError("the recipe is not an object")
    when, then = [], []
    for i, c in enumerate(spec.get("when") or []):
        field, op = str(c.get("field") or ""), str(c.get("op") or "contains")
        value = str(c.get("value") or "").strip()
        if field not in FIELDS or op not in OPS or not value:
            raise ValueError(f"condition {i + 1} is not understood")
        when.append(
            {
                "id": str(c.get("id") or f"c{i + 1}"),
                "field": field,
                "op": op,
                "value": value[:120],
                "text": str(c.get("text") or value)[:160],
            }
        )
    for i, st in enumerate(spec.get("then") or []):
        do = str(st.get("do") or "")
        if do not in STEPS:
            raise ValueError(f"step {i + 1} is not understood")
        step = {
            "id": str(st.get("id") or f"s{i + 1}"),
            "do": do,
            "text": str(st.get("text") or do)[:160],
        }
        if do == "carry_out":
            m = st.get("match") or {}
            step["match"] = {k: str(m[k])[:80] for k in ("to", "action_contains") if m.get(k)}
        if do == "notify":
            ch = str(st.get("channel") or "both")
            step["channel"] = ch if ch in CHANNELS else "both"
            step["message"] = str(st.get("message") or "{headline}")[:200]
        if do == "log":
            step["message"] = str(st.get("message") or st.get("text") or "")[:200]
        then.append(step)
    if not when:
        raise ValueError("a routine needs at least one condition")
    if not then:
        raise ValueError("a routine needs at least one step")
    return {"when": when, "then": then}


COMPILED = """
from soony import open_cards, carry_out, notify, log, first_time

CONDITIONS = {conditions}
STEPS = {steps}


def field_text(card, field):
    if field == "evidence":
        parts = []
        for e in card["evidence"]:
            parts.append(e["source"] + " " + e["value"])
        return " ".join(parts)
    if field == "option_action" or field == "option_to":
        key = "action" if field == "option_action" else "to"
        parts = []
        for o in card["options"]:
            parts.append(o[key] or "")
        return " ".join(parts)
    value = card.get(field)
    return value if isinstance(value, str) else ""


def holds(card, cond):
    text = field_text(card, str(cond["field"])).lower()
    value = str(cond["value"]).lower()
    if cond["op"] == "is":
        return text == value
    if cond["op"] == "any_of":
        for v in value.split(","):
            if v.strip() and v.strip() in text:
                return True
        return False
    return value in text


def fill(template, card):
    return template.replace("{{headline}}", card["headline"] or "").replace(
        "{{supplier}}", card["supplier_name"] or "the supplier"
    )


def pick(card, match):
    for o in card["options"]:
        to = str(match.get("to") or "").lower()
        words = str(match.get("action_contains") or "").lower()
        if to and to not in str(o["to"] or "").lower():
            continue
        if words and words not in str(o["action"] or "").lower():
            continue
        return o
    return None


handled = 0
for card in open_cards():
    matched = True
    for cond in CONDITIONS:
        if not holds(card, cond):
            matched = False
    if not matched or not first_time(card["id"]):
        continue
    for step in STEPS:
        if step["do"] == "carry_out":
            option = pick(card, step.get("match") or {{}})
            if option is None:
                log("No matching option on: " + card["headline"])
            elif option["free"]:
                result = carry_out(card["id"], option["index"], str(step["text"]))
                status = str(result["status"])
                log(str(step["text"]) + ": " + card["headline"] + " (" + status + ")")
            else:
                notify("Needs you, it costs money or changes the plan: " + card["headline"],
                       card["id"], "both")
        elif step["do"] == "notify":
            notify(fill(str(step["message"]), card), card["id"], str(step["channel"]))
        else:
            log(fill(str(step["message"]), card))
    handled += 1
handled
"""


def compile_spec(spec: dict) -> str:
    spec = clean_spec(spec)
    return COMPILED.format(conditions=repr(spec["when"]), steps=repr(spec["then"])).strip() + "\n"


SPEC_HELP = """A routine is a recipe (JSON):
{"when": [conditions, all must hold], "then": [steps, in order]}
condition: {"id": "c1", "field": one of evidence | headline | option_action | option_to |
  supplier_name | kind, "op": contains | is | any_of (comma-separated values),
  "value": "<lowercase words to look for>", "text": "<the condition as a plain phrase>"}
step: {"id": "s1", "do": "carry_out", "match": {"to": "<recipient role or address part>",
  "action_contains": "<words in the option>"}, "text": "<plain phrase>"}
  | {"id": "s2", "do": "notify", "channel": "phone" | "teams" | "both",
     "message": "<what to tell the buyer; may use {headline} and {supplier}>", "text": "<phrase>"}
  | {"id": "s3", "do": "log", "message": "<line for the history>", "text": "<phrase>"}
The system always adds: options that cost money or change the plan are never carried out; the
buyer is told instead. Each card is handled once. Fields: evidence = what the system and the
documents say; option_action / option_to = the proposed actions and their recipients."""


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
    return {
        "name": str(data.get("name") or "New routine")[:80],
        "description": str(data.get("description") or "")[:400],
        "spec": data.get("spec") or {},
        "note": str(data.get("note") or "")[:300],
    }


def _ask_for_spec(prompt: str, fallback: dict | None = None) -> dict:
    """Ask the model for a recipe; one repair round if it is not valid. Returns the parsed draft
    with a cleaned spec and its compiled code."""
    error = ""
    for _ in range(2):
        draft = _parse(
            _complete(prompt + (f"\n\nThat recipe was invalid: {error}. Fix it." if error else ""))
        )
        try:
            draft["spec"] = clean_spec(draft["spec"])
            draft["code"] = compile_spec(draft["spec"])
            return draft
        except ValueError as exc:
            error = str(exc)
    if fallback is not None:
        return {**fallback, "note": f"Could not apply that change: {error}."}
    raise HTTPException(502, f"the agent could not write a valid routine: {error}")


def preview(routine: dict) -> dict:
    """Compile (if it has a recipe) and dry-run a routine on today's inbox."""
    if routine.get("spec"):
        routine["code"] = compile_spec(routine["spec"])
    return run(routine, dry=True)


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
        "approve": f"approved: {act.get('action')} (to {act.get('to')}, {act.get('status')})",
        "escalate": f"escalated it to {act.get('to')}",
        "dismiss": "dismissed it",
    }[act["kind"]]
    prompt = WRITER_PROMPT.format(
        spec_help=SPEC_HELP, card=json.dumps(_card_view(card), ensure_ascii=False), action=action
    )
    draft = _ask_for_spec(prompt)
    draft.update(id=uuid.uuid4().hex[:8], created_from=card_id, enabled=False)
    return {"routine": draft, "preview": run(draft, dry=True)}


def edit(routine: dict, instruction: str) -> dict:
    """Change a routine (draft or saved) as the buyer asks; returns the new version and its test
    run. Nothing is saved here."""
    current = {k: routine.get(k) for k in ("name", "description", "spec")}
    prompt = EDIT_PROMPT.format(
        spec_help=SPEC_HELP,
        routine=json.dumps(current, ensure_ascii=False),
        instruction=instruction.replace('"', "'")[:300],
    )
    new = _ask_for_spec(prompt, fallback={**current, "code": routine.get("code", "")})
    after = {**routine, **{k: new[k] for k in ("name", "description", "spec", "code")}}
    return {"after": after, "note": new.get("note", ""), "preview": run(after, dry=True)}


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
            r["handled"] = int(r.get("handled") or 0) + len(
                {d.get("card") for d in rec.get("did") or []}
            )
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
    spec: dict | None = None
    code: str | None = None
    created_from: str | None = None


class EditRequest(BaseModel):
    routine: Draft
    instruction: str


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
    """Save a new routine or a changed version; a changed one keeps its switch, runs and memory."""
    from ask_soony import api

    data = body.model_dump()
    try:
        if data.get("spec"):
            data["spec"] = clean_spec(data["spec"])
            data["code"] = compile_spec(data["spec"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not data.get("code"):
        raise HTTPException(400, "a routine needs a recipe")
    with _lock:
        items = _all()
        old = next((r for r in items if r.get("id") == body.id), None)
        keep = (
            {k: old.get(k) for k in ("enabled", "created_at", "runs", "seen", "handled")}
            if old
            else {
                "enabled": True,
                "created_at": api.now().isoformat(),
                "runs": [],
                "seen": [],
                "handled": 0,
            }
        )
        items = [r for r in items if r.get("id") != body.id] + [{**data, **keep}]
        _save(items)
    return {"ok": True}


@router.post("/api/routines/edit")
def post_edit(body: EditRequest):
    return edit(body.routine.model_dump(), body.instruction)


@router.post("/api/routines/preview")
def post_preview(body: Draft):
    data = body.model_dump()
    try:
        return {"routine": data, "preview": preview(data)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


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
        r["handled"] = int(r.get("handled") or 0) + len(
            {d.get("card") for d in rec.get("did") or []}
        )
        _save(items)
    return rec


@router.delete("/api/routines/{rid}")
def delete_routine(rid: str):
    with _lock:
        _save([r for r in _all() if r.get("id") != rid])
    return {"ok": True}
