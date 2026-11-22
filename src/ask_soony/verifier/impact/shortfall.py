"""
Price it: from the linked graph to copies at risk.

For every edition still being packed: remaining = day-one demand - packed. For every part in
its bill of materials: needed = remaining x quantity per copy. Usable stock = the latest book
stock minus what the evidence says is not really there or can't be used (live linker issues with
a stock effect). Arriving = inbound already on its way that lands in the DC before packing ends
and is not yet in that stock (see _arriving). Gap = needed - usable - arriving. Everything comes
from the data; nothing is typed in.
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
    # Inbound already on its way that lands before packing ends: counted as available.
    # Each: reference, quantity, eta, status, supplier_id, po_numbers, basis (see _arriving)
    arriving: list[dict] = field(default_factory=list)
    # Inbound due before packing ends that is NOT counted, with the reason (already in stock,
    # shipped short, supplier's paper not read yet, ...). Shown so nobody counts it twice.
    not_counted: list[dict] = field(default_factory=list)

    @property
    def usable(self) -> float:
        return max(self.book_stock - sum(d.units for d in self.deductions), 0)

    @property
    def arriving_in_time(self) -> float:
        return sum(a["quantity"] for a in self.arriving)

    @property
    def gap(self) -> float:
        """Needed minus usable stock minus inbound that lands before packing ends."""
        return max(self.needed - self.usable - self.arriving_in_time, 0)

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

    _arriving(needs, by_kind, launch)

    parts = sorted(needs.values(), key=lambda p: -p.gap)
    result = Shortfall(editions, parts, excluded)
    _add_timing(result, by_kind, launch)
    return result


def _skips(status: str) -> str | None:
    """Why an inbound with this status is not 'on its way', or None if it is."""
    s = status.lower().replace("_", " ")
    if "cancel" in s:
        return "cancelled"
    if "arrived" in s:  # at the port: a fix (collect it), see the at_port supply above
        return "at the port, needs collecting"
    if s in ("delivered", "received", "closed", "completed"):
        return f"status '{status}'"
    return None


def _arriving(
    needs: dict[str, PartNeed], by_kind: dict[str, list[dict]], launch: str | None
) -> None:
    """
    Inbound that lands in the DC before packing ends counts as available for the gap.

    Sources, per part: the launch plan's inbound rows (with quantity) and the ERP's open shipments
    (newest row per reference; quantity from its open PO lines when the plan has no row for it).
    An inbound counts when all of these hold:
    - ETA on or before launch day (ETAs are dates; packing ends on launch day, so a same-day ETA
      is taken as in time; the same cut-off as 'after_launch' above)
    - not cancelled, not at the port (that is a 'collect' fix, not available stock)
    - not already in the book stock: a goods receipt for its PO and part on or before the stock
      date means it is in the stock figure; one after it means it arrived since and counts
    - not overdue: an ETA before the stock date with no receipt is a lost shipment, not supply
    - the supplier's own shipping document for the same air waybill or container does not say
      less: then only the shipped quantity counts and the balance is listed as not counted. If
      that document exists but has not been read yet, nothing counts until it is (it may be a
      short-shipment advice).
    Everything not counted lands in `need.not_counted` with the reason.
    """
    if not launch:
        return
    shipments: dict[str, dict] = {}  # reference -> newest ERP row
    for s in by_kind.get("shipment", []):
        ref = s["reference"] or s["shipment_id"]
        if ref and (ref not in shipments or (s["as_of"] or "") > (shipments[ref]["as_of"] or "")):
            shipments[ref] = s
    docs_by_ref: dict[str, list[dict]] = {}
    for d in by_kind.get("shipping_doc", []):
        if d["reference"]:
            docs_by_ref.setdefault(d["reference"], []).append(d)
    read_paths = {r["doc_path"] for r in by_kind.get("reading", [])}
    lines_by_doc: dict[tuple, list[dict]] = {}
    for line in by_kind.get("doc_line", []):
        # The reader sometimes puts our part number in `item` (or the supplier part number)
        # and the linker leaves material_id empty; fall back so the line is still found.
        attrs = line.get("attrs") or {}
        part_key = line["material_id"] or attrs.get("item") or attrs.get("supplier_part_no")
        lines_by_doc.setdefault((line["doc_path"], part_key), []).append(line)
    receipts: dict[tuple, list[dict]] = {}
    for g in by_kind.get("goods_receipt", []):
        receipts.setdefault((g["po_number"], g["material_id"]), []).append(g)
    open_lines: dict[tuple, list[dict]] = {}
    for p in by_kind.get("po_line", []):
        if (p["attrs"].get("state") or p["attrs"].get("status")) == "open":
            open_lines.setdefault((p["po_number"], p["material_id"]), []).append(p)

    for part, need in needs.items():
        stock_day = (need.book_as_of or "")[:10]
        candidates = []  # (reference, quantity, etd, eta, status, supplier, po_numbers, basis)
        planned = set()
        for plan in by_kind.get("plan_inbound", []):
            if plan["material_id"] != part:
                continue
            ref, a = plan["reference"], plan["attrs"]
            erp = shipments.get(ref)
            eta, status, basis = a.get("eta"), a.get("status") or "", "launch plan"
            if erp:  # the ERP row is time-stamped, the plan is not: it wins on status and ETA
                status = erp["attrs"].get("status") or status
                if erp["attrs"].get("eta") and erp["attrs"]["eta"] != eta:
                    basis += f" (ERP ETA {erp['attrs']['eta']}, plan said {eta})"
                    eta = erp["attrs"]["eta"]
            pos = sorted(
                set((erp or {}).get("po_numbers") or [])
                | {d["po_number"] for d in docs_by_ref.get(ref, []) if d["po_number"]}
            )
            planned.add(ref)
            etd = (erp or {}).get("attrs", {}).get("etd") or a.get("etd")
            candidates.append(
                (ref, a.get("quantity"), etd, eta, status, plan["supplier_id"], pos, basis)
            )
        for ref, s in shipments.items():
            if ref in planned or part not in (s["material_ids"] or []):
                continue
            pos = sorted(s["po_numbers"] or [])
            quantity = sum(
                p["attrs"].get("quantity") or 0
                for po in pos
                for p in open_lines.get((po, part), [])
            )
            a = s["attrs"]
            candidates.append(
                (ref, quantity, a.get("etd"), a.get("eta"), a.get("status") or "",
                 s["supplier_id"], pos, "ERP shipment, open PO lines")
            )  # fmt: skip

        for ref, quantity, etd, eta, status, supplier, pos, basis in candidates:
            entry = {
                "reference": ref,
                "quantity": quantity or 0,
                "eta": eta,
                "status": status,
                "supplier_id": supplier,
                "po_numbers": pos,
                "basis": basis,
            }

            def skip(reason: str, entry: dict = entry) -> None:
                need.not_counted.append({**entry, "reason": reason})

            if not eta or eta[:10] > launch:
                continue  # after launch: listed as 'after_launch' supply above, never available
            if why := _skips(status):
                skip(why)
                continue
            if not quantity:
                skip("no quantity on record")
                continue
            # Receipts of this shipment: same PO and part, booked after it left (an earlier
            # receipt on the same PO is an earlier delivery, not this one)
            grs = [
                g
                for po in pos
                for g in receipts.get((po, part), [])
                if not etd or (g["attrs"].get("gr_date") or "") >= etd[:10]
            ]
            booked = [
                g for g in grs if stock_day and (g["attrs"].get("gr_date") or "") <= stock_day
            ]
            if booked:  # received before the stock figure was taken: already in book stock
                skip(f"already in stock (goods receipt {booked[0]['gr_number']})")
                continue
            if grs:  # received after the stock figure: it is in the DC, count what was received
                entry["quantity"] = sum(g["attrs"].get("qty_received") or 0 for g in grs)
                entry["basis"] += f"; received after the {stock_day} stock figure"
                entry["status"] = "received"
                need.arriving.append(entry)
                continue
            if stock_day and eta[:10] < stock_day:  # should be here by now, but nobody booked it
                skip(f"ETA {eta} passed before the {stock_day} stock figure, no goods receipt")
                continue
            docs = docs_by_ref.get(ref, [])
            unread = [d for d in docs if d["doc_path"] not in read_paths]
            lines = [
                line
                for d in docs
                for line in lines_by_doc.get((d["doc_path"], part), [])
                if line["attrs"]["final"].get("quantity_shipped") is not None
            ]
            if lines:  # the supplier's paper is the latest word on what is on the plane
                on_paper = min(line["attrs"]["final"]["quantity_shipped"] for line in lines)
                if on_paper < entry["quantity"]:
                    expected = [
                        v["raw"]
                        for line in lines
                        for v in line["attrs"]["values"]
                        if v["field"] == "date_expected" and v["raw"]
                    ]
                    skip(f"balance: supplier's shipping document says {on_paper:,.0f} shipped")
                    need.not_counted[-1]["quantity"] = entry["quantity"] - on_paper
                    need.not_counted[-1]["eta"] = expected[-1] if expected else None
                    entry["quantity"] = on_paper
                    entry["basis"] += "; quantity as shipped per the supplier's document"
            elif unread:
                skip(f"supplier's document {unread[0]['doc_path'].rsplit('/', 1)[-1]} not read yet")
                continue
            if entry["quantity"] > 0:
                need.arriving.append(entry)


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
