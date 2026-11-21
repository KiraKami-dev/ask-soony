# Inconsistencies in the Soony data

This file covers Soony's own data: `soony/` (context, erp, logistics, master, receiving). Supplier data gets its own file.

**Status:** *confirmed* means two or more sources disagree and we checked both. *To verify* means it looks wrong but needs one more check.

## Parts shortages the launch plan does not show

| # | Finding | Sources | Impact | Status |
|---|---|---|---|---|
| S1 | **Art books booked 10× too high.** The printed quantity of 80,000 on the delivery note is crossed out and 8,000 is written by hand. The weight (360 kg, 40 packages) fits 8,000. The order was for 8,000. SAP booked 80,000 as "over delivery accepted", and that batch (5000413065) is in stock as 80,000. | `receiving/DC-HAM/20261118_WZ-261116-473.jpg`, `erp/goods_receipts.csv` (5000413065), `erp/purchase_orders_2025H2-2026.csv` (4500318768/3), `erp/stock_snapshot.csv` | Real art book stock is about 19,300 against 25,200 needed: **about 5,900 short**. The plan says "ok, +66,100". **Fix available:** Karton Nowak offers 8,000 art books from stock, on a truck within about 13 h (P6). | confirmed |
| S2 | **Failed statue lot counted as usable.** Inspection found 9 of 125 statues defective, above the limit of 3, so the lot does not meet the quality standard. Receiving still booked it as "unrestricted" because the supplier is active. Using it needs a sign-off from Supplier Quality **and the Category Lead, a role that is currently vacant**. | `receiving/DC-HAM/inspection_report_5000413063.pdf`, `erp/stock_snapshot.csv` (5000413063: 5,600), `context/org_chart_procurement.pdf` | Without the lot, statues are about 21,800 against 25,200: **about 3,400 short**. The plan says "ok, +2,200". | confirmed |
| S3 | **Magnets: needed, but nobody owns them.** The bill of materials needs 4 per Collector's Edition (100,800 in total). Stock is 0, with no open purchase order and no receipt ever. The part is flagged class C and not critical. Its category "Magnets" has no one responsible in the org chart. | `master/bom.csv`, `master/components.csv`, `erp/stock_snapshot.csv`, `erp/purchase_orders_*.csv`, `context/launch_plan_GH6.xlsx` (Parts: "CHECK") | **Resolved by P1:** the magnets come fitted in the Collector's box from PackRight. The BOM and the plan are wrong to list them separately, and "CHECK" is a false alarm. | resolved (false alarm) |
| S4 | **Game cards are only "ok" thanks to one air shipment.** Stock covers 118,400 of the 153,200 needed. The plan reaches "ok" by counting 48,000 cards arriving by air on Sat 21 Nov. | `context/launch_plan_GH6.xlsx` (Parts, Inbound), `erp/stock_snapshot.csv` | **Updated by P2:** only 28,000 of the 48,000 flew, so game cards are **about 6,800 short** across both editions. | confirmed |

## Shipments and logistics

| # | Finding | Sources | Impact | Status |
|---|---|---|---|---|
| S5 | **Air shipment ETA is faster than the standard route.** SHP-2026-04311 (48,000 game cards, Incheon → Frankfurt) departs Fri 20 Nov with an ETA of Sat 21 Nov, i.e. 1 day. The standard route (RT-005) says 2 days, plus 1 day by truck from Frankfurt to the DC (RT-009). That would mean arrival on **Mon 23 Nov, after launch**. | `erp/open_shipments.csv`, `master/routes.csv`, `context/launch_plan_GH6.xlsx` (Inbound) | **Resolved by P2:** the shipping advice confirms a short shipment. 28,000 flew; the 20,000 balance reaches Hamburg Tue 24 Nov. The feed still shows 48,000 on time. | confirmed |
| S6 | **11 of 25 shipments are missing from the Excel tracker.** These are all air and road shipments plus 5 later sea bookings. The tracker was last saved on 18 Nov by Raj Patel. | `logistics/open_shipments_tracker_v37_FINAL_final2.xlsx`, `erp/open_shipments.csv` | Anyone reading only the tracker would miss both launch-critical game card air shipments. | confirmed |
| S7 | **Stale status in the tracker.** SHP-2026-04276 is "in transit" in the tracker but has "arrived at port, available for pick-up" in the feed. SHP-2026-04281 is "in transit" in the tracker but "in delivery" in the feed. | same as S6 | 04276 carries 350,000 game card cases sitting at the Hamburg terminal. Someone still has to collect them and book a dock slot (PP-07 §3). | confirmed |
| S8 | **No quantities in the shipment feed.** Shipments only list order numbers, sometimes several joined with `|`. Quantities have to be inferred from the purchase orders. | `erp/open_shipments.csv` | We can't say exactly how much of a part a given shipment carries. | confirmed (gap) |

## Planning data

| # | Finding | Sources | Impact | Status |
|---|---|---|---|---|
| S9 | **Packing log ends Thu 19 Nov, but the plan is dated Fri 20 Nov 18:00.** The log sums exactly to the plan's "Packed" numbers (512,000 / 46,800), so Friday's output is missing from both. | `erp/packout_log.csv`, `context/launch_plan_GH6.xlsx` | "Still to pack" may be overstated, or Friday wasn't packed. | to verify |
| S10 | **ERP stock is "not counted"** (the plan's own label). The last physical counts are 4 to 19 days old, and magnets were last counted on 1 Nov. | `erp/stock_snapshot.csv`, `context/launch_plan_GH6.xlsx` | S1 shows the booked stock can be badly wrong. | confirmed |
| S11 | **Grand Heist 5 shares parts with GH6.** GH5 uses the same case (CMP-GAM-003) and leaflet (CMP-GAM-011), with 30,000 a week planned and packed at the same DC. The plan's "needed" numbers count GH6 only. | `master/bom.csv`, `master/products.csv`, `context/launch_plan_GH6.xlsx` | Low risk today because cases and leaflets have a large surplus, but it's a blind spot. | confirmed |
| S12 | **Criticality flags understate the risk.** Only the GH6 game card and GH6 sleeve are marked critical. The magnets, statue, steel case and Collector's box are not, yet each one alone can stop the Collector's line. | `master/components.csv`, `master/bom.csv` | Don't use this column for risk ranking. | confirmed |

## Receiving and booking

| # | Finding | Sources | Impact | Status |
|---|---|---|---|---|
| S13 | **Unordered pre-order pins.** 3 receipts for 78,000 "Pre-order Pin GH6 (enamel)" came from Dongguan with no order number and no ordered quantity. The material is not in the master data and was booked as "misc merchandise". | `erp/goods_receipts.csv` (5000412998, 5000413025, 5000413052), `receiving/DC-HAM/*SN-260818-893*`, `*SN-260915-558*`, `*SN-261008-884*` | Unclear whether the pin belongs in a box. It's not in the bill of materials. | confirmed |
| S14 | **Few receipts have scans.** Only 69 of 392 receipts have a scanned delivery note, and scans only exist from Sep 2026 onward. | `receiving/DC-HAM/`, `erp/goods_receipts.csv` | Older bookings can't be checked against paper. Only 3 of the 60 scans have been checked by hand so far. | confirmed |

## Process and ownership gaps

| # | Finding | Sources | Impact | Status |
|---|---|---|---|---|
| S15 | **Category Lead for Collector's Items is vacant.** Under PP-07 this role approves Collector's air freight, extends conditional supplier releases and signs quality concessions (S2). | `context/org_chart_procurement.pdf`, `context/approval_policy_PP-07.pdf`, `context/onboarding_notes.md` | Several Collector's decisions have no one who can make them. They have to be escalated to the Head of Procurement (PP-07 §6). | confirmed |
| S16 | **Two Pune statue orders are stuck in "awaiting approval"** (4500318784 and 4500318791). This matches PP-07 §5: the conditional release probably expired, and only the vacant Category Lead can extend it. | `erp/purchase_orders_2025H2-2026.csv`, `context/approval_policy_PP-07.pdf` | The second statue source is blocked. | to verify (Pune profile and contract) |

## Format quirks (handled in code, not errors)

- **Two purchase-order exports with different layouts.** The legacy file uses SAP field names, `;` as separator, decimal commas, `MM/DD/YYYY` dates, numeric supplier ids (`19` = `SUP-0019`) and no currency column.
- **Cancellations** appear as negative quantities.
- **Singapore is missing.** Routes go "via SGSIN", but Singapore isn't in `master/ports.csv`.
- **The tracker names suppliers**, while the ERP data uses supplier ids.

## To check in the next pass

- **The other 57 delivery-note scans:** look for more handwritten corrections like S1.
- **GH6 sleeve drawing revision B:** are the sleeves in stock and in transit actually revision B?
