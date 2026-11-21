# Inconsistencies in the Soony data

Soony's own data (`soony/`). Supplier data: `inconsistencies_suppliers.md` (P…). Each row compares what the system says with what the evidence says.

## Launch-critical

| # | System says | Evidence says | Impact |
|---|---|---|---|
| S1 | Art books: SAP stock 80,000 (batch 5000413065, "over delivery accepted") | Delivery note `receiving/DC-HAM/20261118_WZ-261116-473.jpg`: 80,000 crossed out, 8,000 handwritten. PO 4500318768/3 is for 8,000. | **About 5,900 short** (plan says "ok"). Fix: Karton has 8,000 in stock (P6). |
| S2 | Statue lot 5000413063 (5,600) is "unrestricted" | `inspection_report_5000413063.pdf`: 9 of 125 defective, limit 3 | **About 3,400 short** (plan says "ok"). A concession needs the vacant Category Lead (S15). |
| S4 | Launch plan counts 48,000 game cards by air, arriving Sat 21 Nov (SHP-2026-04311) | Shipping advice: 28,000 flew; 20,000 arrive Tue 24 Nov (P2). The standard route (S5) also points to Mon 23 Nov. | **About 6,800 short.** 34,800 short if the 28,000 miss Sun 20:00. |
| S3 | BOM: 4 magnets per Collector's Edition, stock 0. The plan shows "CHECK" for 100,800. | PackRight contract Annex A: magnets come fitted in the box (P1) | **False alarm.** |

## Other contradictions

| # | System says | Evidence says | Impact |
|---|---|---|---|
| S6 | Excel tracker: 14 shipments | Shipment feed: 25 | The tracker misses both game card air shipments. |
| S7 | Tracker: SHP-2026-04276 "in transit" | Feed: arrived at port, available for pick-up | 350,000 game card cases waiting at the Hamburg terminal |
| S9 | Plan dated Fri 20 Nov 18:00 | Packing log ends Thu 19 Nov | Friday's output is missing. *To verify.* |
| S13 | 78,000 "Pre-order Pin GH6" booked as misc merchandise | No PO, not in master data, not in the BOM | Unordered goods from Dongguan (P20) |

## Gaps that limit checking

- **S8** The shipment feed has no quantities, only PO numbers. Quantities have to be inferred from the POs.
- **S10** Stock is "not counted": physical counts are 4 to 19 days old.
- **S14** Only 69 of 392 receipts have a scanned delivery note, all from Sep 2026 onward.
- **S11** GH5 uses the same case and leaflet as GH6, but the plan counts GH6 only. Low risk (large surplus).
- **S12** Criticality flags are unreliable: the statue, steel case and Collector's box are not marked critical.

## Ownership gaps

- **S15** The Category Lead for Collector's Items is vacant. Concessions, Collector's air freight and supplier releases escalate to the Head of Procurement (PP-07 §6).
- **S16** Two Pune statue orders are stuck in "awaiting approval" because the conditional release expired (P17).

## Format quirks (handle in code)

- Two PO exports with different layouts. The legacy one uses `;`, decimal commas, `MM/DD/YYYY` dates, numeric supplier ids (`19` = `SUP-0019`) and no currency column.
- Cancellations are negative quantities.
- Routes go via SGSIN, which is missing from `master/ports.csv`.
- The tracker uses supplier names; the ERP uses supplier ids.

## Next

- Check the other 57 delivery-note scans for handwritten corrections like S1. This is the verifier's first job.
