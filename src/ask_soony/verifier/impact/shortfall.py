"""
Price it: from the linked graph to copies at risk.

For every edition still being packed: remaining = day-one demand - packed. For every part in
its bill of materials: needed = remaining x quantity per copy. Usable stock = the latest book
stock minus what the evidence says is not really there or can't be used (live linker issues with
a stock effect). Gap = needed - usable. Everything comes from the data; nothing is typed in.
"""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from ask_soony.verifier.impact.timing import lead_hours, parse_moment, runs_dry, shift_windows

# Linker issues whose units are book stock that is not really usable
STOCK_EFFECT_RULES = {
    "delivery_vs_receipt": "booked but not delivered",
    "failed_lot_in_stock": "failed inspection",
    "remark_names_other_part": "wrong part booked",
    "old_revision_in_stock": "old revision, must not be packed",
}


@dataclass
class Deduction:
    reason: str
    units: float
    issue: str  # record id of the issue's record
    context: str


@dataclass
class PartNeed:
    part: str
    description: str | None
    editions: list[str]
    needed: float
    book_stock: float
    book_as_of: str | None
    deductions: list[Deduction] = field(default_factory=list)
    plan_status: str | None = None  # what the launch plan says ('ok', 'CHECK')
    supply: list[dict] = field(default_factory=list)  # options to close the gap
    per_copy: dict[str, float] = field(default_factory=dict)  # edition → parts per copy
    used_today: float | None = None
    rate_per_hour: float | None = None  # how fast the lines use it
    runs_dry: datetime | None = None  # None: lasts to the end of packing
    fixes: list[dict] = field(default_factory=list)  # supply that can arrive in time, with act-by

    @property
    def usable(self) -> float:
        return max(self.book_stock - sum(d.units for d in self.deductions), 0)

    @property
    def gap(self) -> float:
        return max(self.needed - self.usable, 0)

    @property
    def gap_if_acted(self) -> float:
        """The gap left if every fix that can arrive in time is taken."""
        closable = sum(f["quantity"] for f in self.fixes if f["in_time"])
        return max(self.gap - closable, 0)


@dataclass
class Edition:
    product: str
    name: str | None
    launch_date: str | None
    day_one: float
    packed: float
    packed_as_of: str | None
    rate_per_hour: float | None

    @property
    def remaining(self) -> float:
        return max(self.day_one - self.packed, 0)


@dataclass
class Shortfall:
    editions: list[Edition]
    parts: list[PartNeed]
    excluded: list[dict]  # BOM parts not stocked separately, with the evidence
    now: datetime | None = None  # time of the newest stock figures
    packing_ends: datetime | None = None
    hours_left: float | None = None

    def at_risk(self, product: str, acted: bool = False) -> tuple[float, str | None]:
        """
        Copies of an edition at risk from parts only it uses, and the part that binds.
        acted=True: after every fix that can still arrive in time.
        """

        def gap(p: PartNeed) -> float:
            return p.gap_if_acted if acted else p.gap

        own = [p for p in self.parts if p.editions == [product] and gap(p) > 0]
        if not own:
            return 0, None
        worst = max(own, key=gap)
        return gap(worst), worst.part

    @property
    def shared_gaps(self) -> list[PartNeed]:
        return [p for p in self.parts if len(p.editions) > 1 and p.gap > 0]


def _latest(rows: list[dict]) -> dict | None:
    """The newest record by as_of; records without a date come last."""
    return max(rows, key=lambda r: r["as_of"] or "", default=None)


def _known_parts(records: pd.DataFrame) -> set[str]:
    return set(records[records.kind == "part"].material_id.dropna())


def _fitted_parts(records: pd.DataFrame, known: set[str]) -> dict[str, dict]:
    """
    Parts a supplier says it fits into another part ('fitted by supplier into CMP-...'), so they
    are never stocked on their own. Learned from the suppliers' own part lists.
    """
    fitted = {}
    for r in records[records.kind == "supplier_part"].to_dict("records"):
        note = r["attrs"].get("note") or ""
        others = [p for p in known if p != r["material_id"] and re.search(re.escape(p), note)]
        if others and re.search(r"fitted|included|built[- ]in|assembled", note, re.I):
            fitted[r["material_id"]] = {
                "inside": others[0],
                "evidence": note,
                "source": r["source"],
            }
    return fitted


def compute(records: pd.DataFrame, issues: pd.DataFrame) -> Shortfall:
    rows = records.to_dict("records")
    by_kind: dict[str, list[dict]] = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)

    # Editions: launch products with a day-one demand
    editions = []
    for product in by_kind.get("product", []):
        demand = product["attrs"].get("day_one_qty")
        if not demand:
            continue
        pid = product["material_id"]
        packed = _latest([r for r in by_kind.get("packed_count", []) if r["material_id"] == pid])
        plans = [r for r in by_kind.get("plan_edition", []) if r["material_id"] == pid]
        plan = _latest(plans)
        packed_qty = (packed or {}).get("attrs", {}).get("quantity")
        if packed_qty is None and plan:
            packed_qty = plan["attrs"].get("packed")
        rate = next(
            (p["attrs"]["rate_per_hour"] for p in plans if p["attrs"].get("rate_per_hour")), None
        )
        editions.append(
            Edition(
                pid,
                product["attrs"].get("name"),
                product["attrs"].get("launch_date"),
                demand,
                packed_qty or 0,
                (packed or plan or {}).get("as_of"),
                rate,
            )
        )
    remaining = {e.product: e.remaining for e in editions}

    # Needs from the bill of materials
    known = _known_parts(records)
    fitted = _fitted_parts(records, known)
    needs: dict[str, PartNeed] = {}
    excluded = []
    descriptions = {
        r["material_id"]: r["attrs"].get("description") for r in by_kind.get("part", [])
    }
    for line in by_kind.get("bom_line", []):
        parent, part = line["attrs"]["parent"], line["material_id"]
        if parent not in remaining:
            continue
        if part in fitted:
            excluded.append({"part": part, "edition": parent, **fitted[part]})
            continue
        need = needs.setdefault(part, PartNeed(part, descriptions.get(part), [], 0, 0, None))
        need.editions.append(parent)
        need.per_copy[parent] = line["attrs"].get("qty_per") or 1
        need.needed += remaining[parent] * need.per_copy[parent]

    # Book stock: the newest count per part; else the stock snapshot
    for part, need in needs.items():
        latest = _latest([r for r in by_kind.get("stock_count", []) if r["material_id"] == part])
        if latest:
            need.book_stock, need.book_as_of = latest["attrs"].get("quantity") or 0, latest["as_of"]
            need.used_today = latest["attrs"].get("used_today")
        else:
            batches = [r for r in by_kind.get("stock_batch", []) if r["material_id"] == part]
            need.book_stock = sum(r["attrs"].get("quantity") or 0 for r in batches)
            need.book_as_of = _latest(batches)["as_of"] if batches else None
        plan_part = _latest([r for r in by_kind.get("plan_part", []) if r["material_id"] == part])
        need.plan_status = plan_part["attrs"].get("status") if plan_part else None

    # Deductions: live issues with a stock effect
    for issue in issues.to_dict("records"):
        reason = STOCK_EFFECT_RULES.get(issue["rule"])
        units = issue.get("units")
        if (
            not reason
            or not issue.get("live")
            or units is None
            or (isinstance(units, float) and math.isnan(units))
        ):
            continue
        if issue["material_id"] in needs:
            needs[issue["material_id"]].deductions.append(
                Deduction(reason, float(units), issue["record_id"], issue.get("context") or "")
            )

    # Supply that could close a gap before launch
    launch = min((e.launch_date for e in editions if e.launch_date), default=None)
    for plan in by_kind.get("plan_inbound", []):
        need = needs.get(plan["material_id"])
        status = (plan["attrs"].get("status") or "").lower()
        if need is None:
            continue
        if "arrived" in status:  # at the port, not in stock yet: needs collecting
            need.supply.append(
                {
                    "kind": "at_port",
                    "quantity": plan["attrs"].get("quantity"),
                    "reference": plan["reference"],
                    "note": plan["attrs"].get("comment"),
                    "supplier_id": plan["supplier_id"],
                }
            )
        elif launch and plan["attrs"].get("eta") and plan["attrs"]["eta"] > launch:
            need.supply.append(
                {
                    "kind": "after_launch",
                    "quantity": plan["attrs"].get("quantity"),
                    "reference": plan["reference"],
                    "eta": plan["attrs"]["eta"],
                    "supplier_id": plan["supplier_id"],
                }
            )
    for reading in by_kind.get("reading", []):
        if reading["attrs"].get("doc_type") != "offer":
            continue
        for line in (
            r for r in by_kind.get("doc_line", []) if r["doc_path"] == reading["doc_path"]
        ):
            need = needs.get(line["material_id"])
            final = line["attrs"]["final"]
            quantity = next(
                (
                    final[k]
                    for k in (
                        "quantity_confirmed",
                        "quantity_ordered",
                        "quantity_shipped",
                        "quantity_delivered",
                    )
                    if final.get(k)
                ),
                None,
            )
            if need is not None and quantity:
                need.supply.append(
                    {
                        "kind": "offer",
                        "quantity": quantity,
                        "document": reading["doc_path"],
                        "red_flags": reading["attrs"].get("red_flags", [])[:2],
                        # 'loaded within 4 h of your order' + 'about 9 hours driving'
                        "lead_texts": [
                            v["raw"] for v in line["attrs"]["values"] if v["field"] == "lead_time"
                        ]
                        + [
                            s["text_en"]
                            for s in reading["attrs"].get("statements", [])
                            if s.get("topic") == "shipment"
                        ],
                    }
                )

    parts = sorted(needs.values(), key=lambda p: -p.gap)
    result = Shortfall(editions, parts, excluded)
    _add_timing(result, by_kind, launch)
    return result


def _add_timing(result: Shortfall, by_kind: dict[str, list[dict]], launch: str | None) -> None:
    """
    When each part runs dry at the packing rate, and which fixes can arrive before then.

    'Now' is the time of the newest stock or packed count. A fix's act-by time is when it must be
    ordered or decided so it lands before the line runs dry.
    """
    windows = shift_windows([s["attrs"] for s in by_kind.get("plan_shift", [])], launch)
    stamps = [
        parse_moment(r["as_of"])
        for kind in ("stock_count", "packed_count")
        for r in by_kind.get(kind, [])
        if r["as_of"]
    ]
    now = max((s for s in stamps if s), default=None)
    if not windows or now is None:
        return
    result.now = now
    result.packing_ends = windows[-1][1]
    result.hours_left = (
        sum(max((end - max(start, now)).total_seconds(), 0) for start, end in windows) / 3600
    )
    rates = {e.product: e.rate_per_hour for e in result.editions}
    for need in result.parts:
        rate = sum((rates.get(e) or 0) * q for e, q in need.per_copy.items())
        need.rate_per_hour = rate or None
        if rate:
            need.runs_dry = runs_dry(windows, now, need.usable / rate)
        deadline = need.runs_dry or result.packing_ends
        if not need.gap:
            continue
        for supply in need.supply:
            if supply["kind"] == "at_port":
                need.fixes.append(
                    {
                        "kind": "collect",
                        "quantity": min(supply["quantity"] or 0, need.gap),
                        "available": supply["quantity"],
                        "act_by": deadline,
                        "arrives": None,
                        "in_time": True,
                        "reference": supply.get("reference"),
                        "assumption": "collected and unloaded before the line runs dry",
                    }
                )
            elif supply["kind"] == "offer":
                lead = lead_hours(supply.get("lead_texts", []))
                arrives = now + timedelta(hours=lead) if lead is not None else None
                in_time = arrives is not None and arrives <= result.packing_ends
                need.fixes.append(
                    {
                        "kind": "order",
                        "quantity": min(supply["quantity"], need.gap),
                        "available": supply["quantity"],
                        "lead_hours": lead,
                        "act_by": deadline - timedelta(hours=lead) if lead is not None else None,
                        "arrives": arrives,
                        "in_time": in_time,
                        "document": supply.get("document"),
                    }
                )
        for deduction in need.deductions:
            if deduction.reason == "failed inspection":
                need.fixes.append(
                    {
                        "kind": "concession",
                        "quantity": min(deduction.units, need.gap),
                        "available": deduction.units,
                        "act_by": deadline,
                        "arrives": None,
                        "in_time": True,
                        "issue": deduction.issue,
                    }
                )
