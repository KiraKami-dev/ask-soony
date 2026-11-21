# Findings from the discovery run (21 Nov)

283 of 289 documents were read with `claude-sonnet-5-5`. 6 large scans were skipped (see README, "Not read yet"). The run compared the reader's output with `goods_receipts.csv` and `purchase_orders_2025H2-2026.csv`.

## What the paper says vs SAP: 5 categories

| # | Category | Cases | Examples |
|---|---|---|---|
| 1 | **Booked quantity ≠ paper** | 1 | `WZ-261116-473` art book: paper 8,000 (80.000 crossed out), SAP received 80,000, PO 8,000 (S1). The other 68 booked delivery notes match exactly. |
| 2 | **Arrived but not booked** | 3 | `DN-261120-726`: **28,000 GH6 game cards received 21 Nov** (the P2 air shipment). `WZ-261120-732`: 20,000 GH6 sleeves received 21 Nov. `DN-261119-197`: 10,500 GH5 cards from Boise (5,000 + 5,500). None is in SAP yet. |
| 3 | **Supplier terms ≠ SAP** | ~50 | **Price:** Hwaseong 5.51, 5.56 and 5.62 vs SAP 5.40 (P9). Boise 4500318533 at 6.29 vs SAP 6.11 (new). **Date:** Hwaseong 4500318636 confirms 9 Jun, SAP says 26 May (P10). **Incoterm:** about 40 Boise confirmations say EXW Boise, SAP says DDP Hamburg (P13), and 7 of them also say "freight prepaid by Seller". |
| 4 | **Wrong or different product** | 11 | Karton prints the GH6 sleeve to rev. C (10 confirmations, P3). **New:** `WZ-261120-732` has the handwritten remark "1 Pal. = GH5 Sleeves!", so one of its 10 pallets is the wrong sleeve. |
| 5 | **Status and quality claims** | 7 | Pune claims "approved for serial supply" in 6 confirmations, while SAP has it as conditional (P16). Inspection report 5000413063: lot **failed**, 9 of 125 defective, accept number 3 (S2). |

**Lesson:** SAP is right line by line almost everywhere: 0 quantity mismatches across 436 confirmation values, and 68 of 69 delivery notes match. The problems are rare but critical bookings (category 1), **timing**, where paper is ahead of SAP (category 2), and terms the supplier sets differently (category 3).

## Reader errors: 4 kinds

| Error | Cases | Fix (in the join, no new run needed) |
|---|---|---|
| Thousands dot misread: `5.000` gives `number` 5, mostly on Chinese and English notes | 11 | Re-parse from the raw `value`: a dot or comma followed by exactly 3 digits is a thousands separator. Check against the PO quantity. |
| Signatures and receiving stamps listed as `corrections` | 24 of 25 | Count only corrections with an `original` value |
| Supplier part numbers (`PR-…`) instead of `CMP-…` | 30 PackRight lines | Map with contract Annex C (already read) |
| SDK 1 MB message limit on large files | 6 | Skipped. Raise `max_buffer_size` later. |

Field names stayed stable: only 13 values used `other` (downtime dates, approval limits).

## Not in the linker yet

These results are only in `data/store/image_recognizer/`. Loading them into the linker as claims is the next step (M3: join adapter).
