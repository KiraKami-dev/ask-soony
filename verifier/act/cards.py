"""
Act: one decision card per cause of copies at risk.

A card says what is at risk, why (system vs paper), what could close the gap, and who has to do
something, with the message ready to send. Who to ask follows the routing table in
verifier/README.md: what arrived → Hamburg receiving; what shipped → the supplier; what is
allowed → an approver (PP-07; a vacant role escalates to the Head of Procurement).

Nothing is sent automatically: a person reads the card and clicks send.
"""

from dataclasses import dataclass, field

import pandas as pd

from verifier.impact.shortfall import PartNeed, Shortfall
from verifier.impact.timing import fmt

HAMBURG = "DC Hamburg (receiving and logistics)"
APPROVER = "Head of Procurement (Category Lead is vacant, PP-07 §6)"


@dataclass
class Message:
    to_role: str
    to_email: str | None
    subject: str
    body: str


@dataclass
class Card:
    id: str
    title: str
    edition: str
    at_risk: float  # copies of `edition` this card can save
    part: str
    system_says: str
    paper_says: str
    evidence: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    decision: str | None = None  # what a person has to decide, if anything
    messages: list[Message] = field(default_factory=list)
    at_risk_if_acted: float = 0  # copies still at risk if this card's fix is taken in time
    act_by: str | None = None  # latest time to act so the fix lands before the line runs dry
    runs_dry: str | None = None  # when the line runs out of this part at the packing rate
    blocked_only: bool = False  # stock must be blocked, but there is no gap


def _n(value) -> str:
    return f"{value:,.0f}"


class _Directory:
    """Supplier contacts and documents, from the linked records."""

    def __init__(self, records: pd.DataFrame):
        rows = records.to_dict("records")
        self.suppliers = {}
        for r in rows:  # the newest version of a supplier wins for contact details
            if r["kind"] == "supplier":
                current = self.suppliers.get(r["supplier_id"])
                if current is None or (r["as_of"] or "") > (current["as_of"] or ""):
                    self.suppliers[r["supplier_id"]] = r
        self.parts = {
            r["material_id"]: r["attrs"].get("description") for r in rows if r["kind"] == "part"
        }
        self.primary = {}
        self.prices = {}
        for r in rows:
            if r["kind"] == "supplier_part":
                self.prices[(r["supplier_id"], r["material_id"])] = r["attrs"]
                if r["attrs"].get("primary") == "Y":
                    self.primary.setdefault(r["material_id"], r["supplier_id"])

    def contact(self, supplier_id: str | None) -> tuple[str, str | None, str | None]:
        s = self.suppliers.get(supplier_id) or {"attrs": {}}
        a = s["attrs"]
        return (
            a.get("name") or supplier_id or "supplier",
            a.get("contact_email"),
            a.get("contact_name"),
        )

    def supplier_of_path(self, doc_path: str) -> str | None:
        return next((sid for sid in self.suppliers if sid and f"/{sid}_" in f"/{doc_path}"), None)


def _signature() -> str:
    return "\n\nBest regards,\nSoony Procurement"


def _edition_of(need: PartNeed, result: Shortfall) -> str:
    return need.editions[0] if len(need.editions) == 1 else " + ".join(need.editions)


def build_cards(result: Shortfall, issues: pd.DataFrame, records: pd.DataFrame) -> list[Card]:
    directory = _Directory(records)
    launch = min((e.launch_date for e in result.editions if e.launch_date), default="launch")
    cards: list[Card] = []
    for need in result.parts:
        part, desc = need.part, need.description or need.part
        edition = _edition_of(need, result)
        reasons = {d.reason: d for d in need.deductions}

        # 1. Gap that stock waiting at the port could close
        at_port = [s for s in need.supply if s["kind"] == "at_port"]
        if need.gap and at_port:
            s = at_port[0]
            cards.append(
                Card(
                    id=f"collect-{part}",
                    title=f"Collect {desc} from the port",
                    edition=edition,
                    at_risk=need.gap,
                    part=part,
                    system_says=f"Launch plan: {need.plan_status or 'ok'}, {_n(s['quantity'])} counted as due",
                    paper_says=f"Only {_n(need.usable)} in stock for {_n(need.needed)} still to pack; "
                    f"{_n(s['quantity'])} are at the port ({s.get('note') or 'not collected'})",
                    evidence=[
                        f"Shipment {s['reference']}: {s.get('note') or ''}",
                        f"Book stock {_n(need.book_stock)} (as of {need.book_as_of})",
                    ],
                    options=[
                        f"Collect {_n(s['quantity'])} ({s['reference']}) and book a dock slot today"
                    ],
                    messages=[
                        Message(
                            HAMBURG,
                            None,
                            f"Urgent: collect {s['reference']} ({desc}) for the {launch} pack-out",
                            f"Hello,\n\nshipment {s['reference']} with {_n(s['quantity'])} {desc} ({part}) "
                            f"is at the port and available for pick-up. DC stock is {_n(need.usable)}, and "
                            f"{_n(need.needed)} are still needed for the launch pack-out, so the line stops "
                            f"after about {_n(need.usable)} copies.\n\nPlease arrange pick-up and a dock slot "
                            f"for today and confirm the arrival time." + _signature(),
                        )
                    ],
                )
            )

        # 2. Booked but never delivered: count it, and buy what is offered
        phantom = reasons.get("booked but not delivered")
        if phantom:
            offers = [s for s in need.supply if s["kind"] == "offer"]
            card = Card(
                id=f"phantom-{part}",
                title=f"{desc}: {_n(phantom.units)} booked that never arrived",
                edition=edition,
                at_risk=need.gap,
                part=part,
                system_says=f"Book stock {_n(need.book_stock)}; plan says {need.plan_status or 'ok'}",
                paper_says=phantom.context,
                evidence=[phantom.context, f"Receipt record {phantom.issue}"],
            )
            card.messages.append(
                Message(
                    HAMBURG,
                    None,
                    f"Please count {desc} ({part}) and correct receipt {phantom.issue.split(':')[-1]}",
                    f"Hello,\n\nreceipt {phantom.issue.split(':')[-1]} books more {desc} than the "
                    f"delivery note shows ({phantom.context}).\n\nPlease count {part} physically and "
                    f"correct the receipt. Our evidence says about {_n(need.usable)} are really in stock."
                    + _signature(),
                )
            )
            for offer in offers:
                supplier = directory.supplier_of_path(offer["document"])
                name, email, person = directory.contact(supplier)
                price = directory.prices.get((supplier, part), {})
                value = (price.get("unit_price") or 0) * offer["quantity"]
                worth = (
                    f" (about {value:,.0f} {price.get('currency') or ''} at list price)"
                    if value
                    else ""
                )
                card.options.append(
                    f"Accept {name}'s offer: {_n(offer['quantity'])} from stock{worth}"
                )
                card.messages.append(
                    Message(
                        f"{name} ({person or 'sales'})",
                        email,
                        f"Order: {_n(offer['quantity'])} {desc} from stock, per your offer",
                        f"Dear {person or 'Sir or Madam'},\n\nthank you for your offer. We would like to "
                        f"order the {_n(offer['quantity'])} {desc} ({part}) you have in stock, at the "
                        f"normal price, for delivery to DC Hamburg as soon as possible.\n\nPlease confirm "
                        f"the loading time and the truck's arrival time. A PO follows."
                        + _signature(),
                    )
                )
            cards.append(card)

        # 3. A failed lot: someone has to decide on a concession
        failed = reasons.get("failed inspection")
        if failed:
            cards.append(
                Card(
                    id=f"concession-{part}",
                    title=f"{desc}: decide on the failed lot",
                    edition=edition,
                    at_risk=need.gap,
                    part=part,
                    system_says=f"{_n(failed.units)} counted as usable stock",
                    paper_says=f"Inspection failed: {failed.context}",
                    evidence=[failed.context],
                    options=[
                        f"Concession: use the {_n(failed.units)} after sorting out defects",
                        f"No concession: {_n(need.gap)} copies short",
                    ],
                    decision=f"Use the failed lot under a concession, or accept {_n(need.gap)} fewer copies?",
                    messages=[
                        Message(
                            APPROVER,
                            None,
                            f"Decision needed today: failed lot of {desc} ({part})",
                            f"Hello,\n\nthe lot ({failed.issue.split(':')[-1]}) failed incoming inspection: "
                            f"{failed.context}.\n\nWithout it we are {_n(need.gap)} {desc} short for the "
                            f"launch pack-out. The Category Lead role is vacant, so per PP-07 the decision on a "
                            f"concession sits with you.\n\nPlease decide: concession (sort and use) or no "
                            f"concession." + _signature(),
                        )
                    ],
                )
            )

        # 4. Wrong part or old revision booked: correct the booking (no copies at risk if covered)
        for reason in ("wrong part booked", "old revision, must not be packed"):
            d = reasons.get(reason)
            if d:
                cards.append(
                    Card(
                        id=f"{reason.split()[0]}-{part}",
                        title=f"{desc}: {_n(d.units)} {reason}",
                        edition=edition,
                        at_risk=need.gap,
                        part=part,
                        system_says=f"{_n(d.units)} counted as usable {part}",
                        paper_says=d.context,
                        evidence=[d.context],
                        options=["Block or rebook it so the line doesn't pack it"],
                        messages=[
                            Message(
                                HAMBURG,
                                None,
                                f"Please block/rebook: {_n(d.units)} {desc} ({reason})",
                                f"Hello,\n\n{d.context}.\n\nPlease block it for the GH6 pack-out and correct "
                                f"the booking. After this we still have {_n(need.usable)} usable for "
                                f"{_n(need.needed)} needed." + _signature(),
                            )
                        ],
                    )
                )

    # 5. A shared part is short: decide who gets it, and ask the supplier for more
    for need in result.shared_gaps:
        desc = need.description or need.part
        short = [
            i
            for i in issues.to_dict("records")
            if i["rule"] == "plan_vs_paper" and i["material_id"] == need.part
        ]
        supplier = None
        if short:
            reading = records[records.record_id == short[0]["related"]]
            supplier = (
                directory.supplier_of_path(reading.doc_path.iloc[0]) if len(reading) else None
            )
        supplier = supplier or directory.primary.get(need.part)
        name, email, person = directory.contact(supplier)
        later = [s for s in need.supply if s["kind"] == "after_launch"]
        card = Card(
            id=f"allocate-{need.part}",
            title=f"{desc}: {_n(need.gap)} short across {len(need.editions)} editions",
            edition=" + ".join(need.editions),
            at_risk=need.gap,
            part=need.part,
            system_says=short[0]["context"] if short else f"Plan says {need.plan_status or 'ok'}",
            paper_says=f"{_n(need.usable)} in stock for {_n(need.needed)} still to pack",
            evidence=[i["context"] for i in short] + [f"Book stock {_n(need.book_stock)}"],
            options=[
                f"{s['reference']}: {_n(s['quantity'])} arriving {s['eta']} (after launch)"
                for s in later
            ],
            decision=f"Which edition gets the {_n(need.usable)} {desc}?",
        )
        card.messages.append(
            Message(
                APPROVER,
                None,
                f"Decision needed: allocate {desc} between editions",
                f"Hello,\n\nwe have {_n(need.usable)} {desc} for {_n(need.needed)} still to pack "
                f"({', '.join(need.editions)}): {_n(need.gap)} short. Please decide which edition gets "
                f"them first." + _signature(),
            )
        )
        if email:
            card.messages.append(
                Message(
                    f"{name} ({person or 'sales'})",
                    email,
                    f"{desc} ({need.part}): any quantity earlier than planned?",
                    f"Dear {person or 'Sir or Madam'},\n\nwe are {_n(need.gap)} {desc} short for our launch "
                    f"pack-out, which ends on {launch}. Can any part of the open balance reach DC Hamburg "
                    f"earlier, for example on an earlier flight? Please reply with quantity and arrival "
                    f"time." + _signature(),
                )
            )
        cards.append(card)

    _add_timing(cards, result)
    return sorted(cards, key=lambda c: (-c.at_risk, c.act_by is None))


# Which fix a card stands for, by the start of its id
FIX_OF_CARD = {"collect": "collect", "phantom": "order", "concession": "concession"}


def _add_timing(cards: list[Card], result: Shortfall) -> None:
    """Deadlines and before/after numbers on every card, and the deadline in its messages."""
    needs = {p.part: p for p in result.parts}
    for card in cards:
        need = needs.get(card.part)
        if need is None:
            continue
        card.runs_dry = fmt(need.runs_dry) if need.runs_dry else None
        kind = FIX_OF_CARD.get(card.id.split("-")[0])
        fix = next((f for f in need.fixes if f["kind"] == kind), None)
        if fix:
            card.at_risk_if_acted = max(
                card.at_risk - (fix["quantity"] if fix["in_time"] else 0), 0
            )
            card.act_by = fmt(fix["act_by"]) if fix["act_by"] else None
            if fix.get("lead_hours"):
                card.options.append(
                    f"Lead time {fix['lead_hours']:.0f} h: ordered now it arrives {fmt(fix['arrives'])}"
                    f"{'' if fix['in_time'] else ', too late'}"
                )
        elif card.id.startswith("allocate"):
            card.at_risk_if_acted = need.gap_if_acted
            card.act_by = card.runs_dry  # decide before the shared part runs out
        else:
            card.at_risk_if_acted = card.at_risk
        if card.at_risk == 0:
            card.blocked_only = True
            used = f"{need.used_today:,.0f}" if need.used_today else "some"
            question = (
                f"\n\nToday the lines used {used} {need.description or need.part}. Which batches "
                f"did they pull, and do any packed copies already contain this stock?"
            )
            for message in card.messages:
                message.body = message.body.replace(
                    "\n\nBest regards", question + "\n\nBest regards"
                )
        if card.act_by:
            for message in card.messages:
                message.body = message.body.replace(
                    "\n\nBest regards",
                    f"\n\nThis is time-critical: we need an answer by {card.act_by}."
                    "\n\nBest regards",
                )
