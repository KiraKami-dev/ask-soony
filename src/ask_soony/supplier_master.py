"""
Supplier master changes: what a newer supplier profile says that the master data does not, and
notices that change a supplier's bank details.

The CSV profile (`suppliers/<id>/profile.csv`) is the master record. Any other profile file next
to it (`profile.json` today, any layout) is a newer export: a different status, qualification,
contact or note there becomes a card when the supplier delivers a launch part. A contact address
that only the newer profile names is not a send target until a person confirms it (approving the
contact card's "trust" option), so a forged or mistyped profile cannot redirect supplier mail.

A notice that announces new bank details is a red card whatever it says: the IBAN is compared
with the contract and the profiles, and the card asks to verify by phone on the number on file
(never the number in the notice) and to hold payments. Nothing here is ever accepted on its own.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?\b")
PHONE = re.compile(r"\+\d[\d ()/-]{6,}\d")
# A notice is about bank details when its name or text says so, in the suppliers' languages.
BANK_WORDS = re.compile(
    r"\b(?:bank|iban|swift|bic|konto|rachunek|bankverbindung|remittance)\b|银行|账户|계좌|은행",
    re.I,
)
CHANGE_WORDS = re.compile(
    r"\b(?:chang\w*|new|update\w*|neue\w*|nowy|nowe|zmian\w*)\b|变更|更改|新|변경|새", re.I
)
FIELDS = ("status", "qualification", "valid_until", "contact_name", "email", "phone", "notes")
LABEL = {
    "status": "status",
    "qualification": "qualification",
    "valid_until": "qualification valid until",
    "contact_name": "contact",
    "email": "contact address",
    "phone": "phone",
    "notes": "notes",
}
FINE_STATUS = {"", "active", "approved", "released", "qualified", "ok"}
TRUST_OPTION = 1  # the contact card's option that makes a new address a send target


# ---------------------------------------------------------------- reading profiles


def _flat(x: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(x, dict):
        out: dict[str, Any] = {}
        for k, v in x.items():
            out.update(_flat(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    if isinstance(x, list):
        out = {}
        for i, v in enumerate(x):
            out.update(_flat(v, f"{prefix}.{i}"))
        return out
    return {prefix: x}


def normalize(flat: dict[str, Any]) -> dict[str, str]:
    """Any profile layout (CSV row, nested JSON) to {status, qualification, valid_until,
    contact_name, email, phone, notes}, matched by key names, never by position."""
    out = dict.fromkeys(FIELDS, "")
    for key, value in flat.items():
        if value in (None, ""):
            continue
        k, v = key.lower().replace("_", "").replace("-", ""), str(value).strip()
        last = k.split(".")[-1]
        if "qualification" in k and last in ("status", "qualificationstatus", "qualification"):
            out["qualification"] = out["qualification"] or v
        elif "valid" in last and ("until" in last or "to" in last):
            out["valid_until"] = out["valid_until"] or v
        elif last == "status" and "qualification" not in k:
            out["status"] = out["status"] or v
        elif "phone" in last or last in ("tel", "telephone"):
            out["phone"] = out["phone"] or v
        elif last in ("contactname",) or ("contact" in k and last == "name"):
            out["contact_name"] = out["contact_name"] or v
        elif last in ("notes", "note", "remarks"):
            out["notes"] = out["notes"] or v
        if not out["email"] and EMAIL.fullmatch(v):
            out["email"] = v.lower()
    return out


def _read_profile(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    if path.suffix.lower() == ".csv":
        rows = list(csv.DictReader(io.StringIO(text)))
        return dict(rows[0]) if rows else None
    if path.suffix.lower() == ".json":
        try:
            return _flat(json.loads(text))
        except ValueError:
            return None
    found = EMAIL.search(text)  # any other layout: at least its address
    return {"contact_email": found.group(0)} if found else None


def profiles(root: Path) -> dict[str, dict]:
    """supplier id -> {dir, name, master: normalized CSV row, newer: [{file, fields}]}."""
    out: dict[str, dict] = {}
    base = root / "suppliers"
    for d in sorted(base.iterdir()) if base.is_dir() else []:
        if not d.is_dir():
            continue
        sid = d.name.split("_")[0]
        master_raw = _read_profile(d / "profile.csv") or {}
        newer = []
        for p in sorted(d.glob("profile.*")):
            if p.name == "profile.csv":
                continue
            raw = _read_profile(p)
            if raw:
                newer.append({"file": str(p.relative_to(root)), "fields": normalize(raw)})
        name = str(master_raw.get("name") or d.name.split("_", 1)[-1].replace("_", " "))
        out[sid] = {
            "dir": d,
            "name": name,
            "master": normalize(master_raw) if master_raw else None,
            "master_file": str((d / "profile.csv").relative_to(root)) if master_raw else None,
            "newer": newer,
        }
    return out


def changes(prof: dict) -> list[dict]:
    """Fields a newer profile states differently from the master record."""
    base = prof.get("master")
    if not base:  # no master record: the first profile is the baseline
        return []
    out = []
    for n in prof["newer"]:
        for f in FIELDS:
            new, old = n["fields"].get(f, ""), base.get(f, "")
            if new and new.strip().lower() != old.strip().lower():
                out.append({"field": f, "old": old, "new": new, "file": n["file"]})
    return out


# ---------------------------------------------------------------- trusted addresses


def _approved(root: Path | None = None) -> set[str]:
    """Addresses a person confirmed: the trust option of a contact card was approved."""
    from ask_soony import api

    out = set()
    for a in api.read_list("actions.json"):
        did = str(a.get("decision_id") or "")
        if (
            did.startswith("SUPM-contact-")
            and a.get("kind") == "approve"
            and a.get("option") == TRUST_OPTION
        ):
            m = EMAIL.search(str(a.get("draft") or "") + " " + str(a.get("action") or ""))
            if m:
                out.add(m.group(0).lower())
    return out


def trusted_addresses(root: Path | None) -> set[str]:
    """Send targets: every address in the master profiles, addresses a newer profile repeats,
    and new addresses a person confirmed. A new address only in a newer profile is left out."""
    if root is None:
        return set()
    found: set[str] = set()
    approved = _approved(root)
    for prof in profiles(root).values():
        base = prof.get("master")
        if base:
            master_text = (prof["dir"] / "profile.csv").read_text(
                encoding="utf-8", errors="replace"
            )
            found |= {m.lower() for m in EMAIL.findall(master_text)}
        for n in prof["newer"]:
            text = Path(root / n["file"]).read_text(encoding="utf-8", errors="replace")
            for m in EMAIL.findall(text):
                m = m.lower()
                if not base or m in found or m in approved:
                    found.add(m)
    return found


def untrusted_addresses(root: Path | None) -> set[str]:
    """Addresses only a newer profile names and nobody has confirmed yet."""
    if root is None:
        return set()
    every: set[str] = set()
    for prof in profiles(root).values():
        for n in prof["newer"]:
            text = Path(root / n["file"]).read_text(encoding="utf-8", errors="replace")
            every |= {m.lower() for m in EMAIL.findall(text)}
    return every - trusted_addresses(root)


# ---------------------------------------------------------------- launch relevance


def launch_parts(root: Path) -> dict[str, list[str]]:
    """Launch part -> launch products using it, from products.csv and bom.csv."""
    from ask_soony import api

    master = root / "soony" / "master"
    launch = {
        r["product_id"]
        for r in api.read_csv(master / "products.csv")
        if r.get("product_id") and str(r.get("lifecycle") or "").lower() == "launch"
    }
    out: dict[str, list[str]] = {}
    for f in sorted(master.glob("bom*.csv")):
        for r in api.read_csv(f):
            if r.get("parent_id") in launch and r.get("child_id"):
                eds = out.setdefault(r["child_id"], [])
                if r["parent_id"] not in eds:
                    eds.append(r["parent_id"])
    return out


def supplied(prof: dict, root: Path) -> list[dict]:
    """The launch parts this supplier delivers: [{part, name, share, editions}]."""
    from ask_soony import api

    lp = launch_parts(root)
    names = api.part_names(root)
    out = []
    for f in sorted(prof["dir"].glob("components*.csv")):
        for r in api.read_csv(f):
            cid = r.get("component_id")
            if cid in lp and not any(o["part"] == cid for o in out):
                share = api.num(r.get("share"))
                out.append(
                    {
                        "part": cid,
                        "name": names.get(cid) or r.get("description") or cid,
                        "share": share,
                        "editions": lp[cid],
                    }
                )
    return out


def _parts_text(parts: list[dict]) -> str:
    """'Statue (20%), Art Book': the launch parts and this supplier's share of each."""
    bits = []
    for p in parts[:3]:
        share = p["share"]
        pct = f" ({share:.0%})" if share is not None and 0 < share <= 1 else ""
        bits.append(f"{p['name']}{pct}")
    more = len(parts) - 3
    return ", ".join(bits) + (f" and {more} more" if more > 0 else "")


# ---------------------------------------------------------------- bank detail notices


def doc_text(path: Path, rel: str) -> str:
    """Text of any portal document through the anchor index (PDF text, OCR of scans, sheets)."""
    from ask_soony import anchors

    units = anchors.index_file(path, rel)
    return "\n".join(u.get("text") or " ".join(w[0] for w in u.get("words", [])) for u in units)


def _ibans(text: str) -> set[str]:
    return {m.replace(" ", "") for m in IBAN.findall(text)}


def bank_notices(root: Path) -> list[dict]:
    """Supplier documents (outside contracts/) that announce new bank details."""
    out = []
    base = root / "suppliers"
    for p in sorted(base.rglob("*")) if base.is_dir() else []:
        rel = str(p.relative_to(root))
        parts = Path(rel).parts
        if (
            not p.is_file()
            or len(parts) < 3
            or "contracts" in parts
            or p.name.startswith("profile.")
        ):
            continue
        name_hit = bool(BANK_WORDS.search(p.stem.replace("_", " ")))
        if not name_hit and p.suffix.lower() not in (
            ".pdf",
            ".txt",
            ".md",
            ".png",
            ".jpg",
            ".jpeg",
        ):
            continue
        text = doc_text(p, rel)
        ibans = _ibans(text)
        if not (name_hit or (ibans and BANK_WORDS.search(text) and CHANGE_WORDS.search(text))):
            continue
        out.append(
            {
                "file": rel,
                "supplier_id": parts[1].split("_")[0],
                "ibans": sorted(ibans),
                "phones": sorted({re.sub(r"\s+", " ", m) for m in PHONE.findall(text)}),
                "quote": next(
                    (ln.strip() for ln in text.splitlines() if "IBAN" in ln.upper()), text[:160]
                )[:200],
            }
        )
    return out


def contract_ibans(prof: dict, root: Path) -> tuple[set[str], list[str]]:
    """IBANs on file (contracts, profiles) and the contract files read."""
    found: set[str] = set()
    files = []
    for p in (
        sorted((prof["dir"] / "contracts").glob("*"))
        if (prof["dir"] / "contracts").is_dir()
        else []
    ):
        if p.is_file():
            rel = str(p.relative_to(root))
            files.append(rel)
            found |= _ibans(doc_text(p, rel))
    for p in prof["dir"].glob("profile.*"):
        found |= _ibans(p.read_text(encoding="utf-8", errors="replace"))
    return found, files


# ---------------------------------------------------------------- cards


def _cid(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:8]


def _expired(valid_until: str, cur: datetime) -> bool:
    try:
        return datetime.fromisoformat(valid_until[:10]).date() < cur.date()
    except ValueError:
        return False


def _fmt_day(value: str) -> str:
    try:
        return datetime.fromisoformat(value[:10]).strftime("%-d %b")
    except ValueError:
        return value


def _short(text: str, n: int = 160) -> str:
    text = " ".join(str(text).split()).rstrip(".")
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _diff_text(d: dict) -> str:
    return f"{LABEL[d['field']]} {_short(d['old']) or '(empty)'} -> {_short(d['new'])}"


def _names(files: list[str]) -> str:
    return ", ".join(Path(c).name for c in files)


def cards(net: dict, cur: datetime, statuses: dict, viewer: str | None) -> list[dict]:
    """Supplier status, contact and bank detail cards, in the inbox's card shape."""
    from ask_soony import api

    root = net.get("root")
    if root is None:
        return []
    root = Path(root)
    out: list[dict] = []
    profs = profiles(root)
    who = api.first_name(viewer) or "the buyer"
    for sid, prof in profs.items():
        diffs = changes(prof)
        if not diffs:
            continue
        parts = supplied(prof, root)
        base = prof["master"] or {}
        latest = {f: base.get(f, "") for f in FIELDS}
        for d in diffs:
            latest[d["field"]] = d["new"]
        status, qual, until = latest["status"], latest["qualification"], latest["valid_until"]
        risky = (
            status.lower() not in FINE_STATUS
            or qual.lower() not in FINE_STATUS
            or bool(until and _expired(until, cur))
        )
        level = ("amber" if risky else "grey") if parts else "grey"
        said = [d for d in diffs if d["field"] not in ("email",)]
        if said:
            bits = []
            if any(d["field"] == "status" for d in said):
                bits.append(f"is now '{status}'")
            if qual and (
                qual.lower() not in FINE_STATUS or any(d["field"] == "qualification" for d in said)
            ):
                bits.append(f"qualification {qual}")
            if until:
                bits.append(
                    f"{'expired' if _expired(until, cur) else 'valid until'} {_fmt_day(until)}"
                )
            head = f"{prof['name']} {', '.join(bits) or 'changed in its newer profile'}"
            if parts:
                head += f". Supplies {_parts_text(parts)}"
            cid = "SUPM-status-" + sid + "-" + _cid(*(f"{d['field']}={d['new']}" for d in said))
            st, ui = api._ui_status(cid, statuses, "open" if level == "amber" else "logged")
            out.append(
                api._base_card(
                    cid,
                    kind="supplier_master",
                    part=parts[0]["part"] if parts else None,
                    headline=head,
                    summary=(
                        f"The newer supplier profile says what the master data (profile.csv) "
                        f"does not: {'; '.join(_diff_text(d) for d in said[:5])}."
                    ),
                    level=level,
                    status=st,
                    ui=ui,
                    reason=(
                        "A supplier on watch or with an expired qualification may not be released "
                        "for launch parts; confirm with Quality before counting on its deliveries."
                        if risky and parts
                        else "Master data and the newer profile disagree; correct the master."
                    ),
                    created_at=cur.isoformat(timespec="seconds"),
                    supplier_id=sid if sid in (net.get("nodes") or {}) else None,
                    supplier_name=prof["name"],
                    edition=(parts[0]["editions"][0] if parts and parts[0]["editions"] else None),
                    evidence=[
                        {
                            "source": d["file"],
                            "value": f"{LABEL[d['field']]}: {d['new']}",
                            "quote": f"master ({prof['master_file']}): {d['old'] or '(empty)'}",
                        }
                        for d in said
                    ],
                    options=[
                        {
                            "index": 0,
                            "recommended": True,
                            "action": f"Ask Quality whether {prof['name']} is still released "
                            "for the launch parts",
                            "cost": "No cost",
                            "cost_value": None,
                            "effect": "know before counting on its deliveries",
                            "draft": f"{prof['name']}'s newer profile says: "
                            + "; ".join(f"{LABEL[d['field']]} {d['new']}" for d in said[:5])
                            + ". The master data still says otherwise. Is it released for "
                            + (_parts_text(parts) or "its parts")
                            + "?",
                            "to": viewer or None,
                            "channel": "notify",
                            "routine_type": None,
                        },
                        {
                            "index": 1,
                            "recommended": False,
                            "action": "Correct the supplier master data",
                            "cost": "No cost",
                            "cost_value": None,
                            "effect": "master data shows the current status",
                            "draft": None,
                            "to": viewer or None,
                            "channel": "notify",
                            "routine_type": None,
                        },
                    ],
                    trail=[
                        {
                            "agent": "Supplier master",
                            "at": cur.isoformat(timespec="seconds"),
                            "text": f"Compared {', '.join(sorted({d['file'] for d in said}))} "
                            f"with {prof['master_file']}.",
                        }
                    ],
                )
            )
        for d in [d for d in diffs if d["field"] == "email"]:
            if d["new"] in trusted_addresses(root):
                continue
            cid = "SUPM-contact-" + sid + "-" + _cid(d["new"])
            st, ui = api._ui_status(cid, statuses, "open")
            phone = base.get("phone")
            out.append(
                api._base_card(
                    cid,
                    kind="supplier_master",
                    headline=f"New contact address for {prof['name']}: not used until confirmed",
                    summary=(
                        f"{d['file']} names {d['new']}; the master data has "
                        f"{d['old'] or 'no address'}. Mail keeps going to the known address "
                        "until someone confirms the new one by phone."
                    ),
                    level="amber" if parts else "grey",
                    status=st,
                    ui=ui,
                    reason="A changed contact address is a common way to redirect orders and "
                    "invoices; confirm it on the number on file first.",
                    created_at=cur.isoformat(timespec="seconds"),
                    supplier_id=sid if sid in (net.get("nodes") or {}) else None,
                    supplier_name=prof["name"],
                    evidence=[
                        {"source": d["file"], "value": f"contact address: {d['new']}", "quote": ""},
                        {
                            "source": prof["master_file"],
                            "value": f"contact address: {d['old'] or '(empty)'}",
                            "quote": "",
                        },
                    ],
                    options=[
                        {
                            "index": 0,
                            "recommended": True,
                            "action": f"Call {base.get('contact_name') or prof['name']} on the "
                            f"number on file{f' ({phone})' if phone else ''} to confirm",
                            "cost": "No cost",
                            "cost_value": None,
                            "effect": "the address is checked before anything goes there",
                            "draft": f"Confirm with {prof['name']} by phone whether {d['new']} "
                            "is their new contact address.",
                            "to": viewer or None,
                            "channel": "notify",
                            "routine_type": None,
                        },
                        {
                            "index": TRUST_OPTION,
                            "recommended": False,
                            "action": f"Confirmed: use {d['new']} for {prof['name']}",
                            "cost": "No cost",
                            "cost_value": None,
                            "effect": "supplier mail may go to the new address",
                            "draft": f"Trusted contact {d['new']} for {sid}.",
                            "to": viewer or None,
                            "channel": "notify",
                            "routine_type": None,
                        },
                    ],
                    trail=[
                        {
                            "agent": "Supplier master",
                            "at": cur.isoformat(timespec="seconds"),
                            "text": f"{who.capitalize()} has not confirmed {d['new']} yet.",
                        }
                    ],
                )
            )
    for n in bank_notices(root):
        prof = profs.get(n["supplier_id"])
        if not prof:
            continue
        on_file, contracts = contract_ibans(prof, root)
        new = [i for i in n["ibans"] if i not in on_file]
        if n["ibans"] and not new:
            continue  # the notice repeats the IBAN on file
        base = prof["master"] or {}
        phone = base.get("phone")
        other_phone = [
            p for p in n["phones"] if phone and p.replace(" ", "") != phone.replace(" ", "")
        ]
        cid = "SUPM-bank-" + n["supplier_id"] + "-" + _cid(n["file"], *n["ibans"])
        st, ui = api._ui_status(cid, statuses, "open")
        if on_file:
            head = "Possible invoice fraud: IBAN differs from the contract"
        elif contracts:
            head = "Possible invoice fraud: new bank details not in the contract"
        else:
            head = "Possible invoice fraud: new bank details, no contract on file"
        out.append(
            api._base_card(
                cid,
                kind="bank_details",
                headline=f"{head} ({prof['name']})",
                summary=(
                    f"{n['file']} asks to pay {prof['name']} to "
                    f"{', '.join(new) or 'a new account (IBAN not readable)'}. "
                    + (
                        f"The contract has {', '.join(sorted(on_file))}. "
                        if on_file
                        else f"No IBAN in {_names(contracts) or 'any contract'} "
                        "or the profiles to compare with. "
                    )
                    + "Do not change the vendor master or pay to it until verified by phone."
                ),
                level="red",
                status=st,
                ui=ui,
                severity="high",
                reason="Bank detail changes by notice are the classic invoice fraud. Verify on "
                "the number on file, never on the number in the notice.",
                created_at=cur.isoformat(timespec="seconds"),
                supplier_id=n["supplier_id"]
                if n["supplier_id"] in (net.get("nodes") or {})
                else None,
                supplier_name=prof["name"],
                evidence=[
                    {
                        "source": n["file"],
                        "value": "new IBAN: " + (", ".join(new) or "?"),
                        "quote": n["quote"],
                    },
                    *(
                        [
                            {
                                "source": c,
                                "value": "contract IBAN: " + (", ".join(sorted(on_file)) or "none"),
                                "quote": "",
                            }
                            for c in contracts[:1]
                        ]
                    ),
                    *(
                        [
                            {
                                "source": n["file"],
                                "value": "phone in the notice differs from the one on file",
                                "quote": ", ".join(other_phone),
                            }
                        ]
                        if other_phone
                        else []
                    ),
                ],
                options=[
                    {
                        "index": 0,
                        "recommended": True,
                        "action": f"Call {base.get('contact_name') or prof['name']} on the number "
                        f"on file{f' ({phone})' if phone else ''}, not the one in the notice",
                        "cost": "No cost",
                        "cost_value": None,
                        "effect": "confirms or exposes the change before any payment",
                        "draft": f"Verify by phone with {prof['name']} on "
                        f"{phone or 'the number on file'} whether they changed their bank "
                        f"account ({n['file']}).",
                        "to": viewer or None,
                        "channel": "notify",
                        "routine_type": None,
                    },
                    {
                        "index": 1,
                        "recommended": False,
                        "action": f"Hold all payments to {prof['name']} until verified",
                        "cost": "No cost",
                        "cost_value": None,
                        "effect": "no money goes to an unverified account",
                        "draft": f"Please hold payments to {prof['name']} ({n['supplier_id']}): a "
                        "bank details change notice is being verified.",
                        "to": viewer or None,
                        "channel": "notify",
                        "routine_type": None,
                    },
                ],
                trail=[
                    {
                        "agent": "Supplier master",
                        "at": cur.isoformat(timespec="seconds"),
                        "text": f"Read {n['file']} and compared it with "
                        f"{', '.join(contracts) or 'the profiles'}.",
                    }
                ],
                trusted_types=[],  # never acted on automatically
            )
        )
    return out
