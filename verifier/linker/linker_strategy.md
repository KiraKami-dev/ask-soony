# Linker strategy

The linker connects every record that describes the same order, delivery or lot, so later steps can compare them. Keys and record types: `../README.md`, section 1.

## Build order

| Step | What | Finds | Status |
|---|---|---|---|
| **M1** | Cleanup functions and spreadsheet adapters. Links within SAP (PO ↔ receipt ↔ batch ↔ stock, PO ↔ shipment ↔ tracker) | Tracker gaps (S6), pins with no PO (S13), supplier profiles whose CSV and JSON versions disagree | ✅ done |
| **M1.1** | Review fixes: versions of one entity kept side by side with their differences; shipment links marked `po_level`; PO line state derived from facts; issue context; supplier and material agreement check; trace hops | Pune `active` vs `watch`, PackRight `active` vs `at risk` | ✅ done |
| **M2** | Filename-level document links: confirmations, shipping documents, delivery-note scans, inspection reports | Unconfirmed PO lines (P7, P12, P15), receipts with no scan (S14), inspection ↔ batch (S2 chain) | next |
| **M3** | Content-level links: confirmation line items, air waybill inside the shipping advice, PackRight part numbers (Annex C) | Line-level PO ↔ confirmation; shipping advice ↔ feed (P2 chain) | planned |
| **M4** | Fuzzy fallback (same supplier, part, quantity, date window), marked for review | Links where keys are missing | planned |

`trace <PO>`, originally planned for M4, was built in M1.

## Principles

- **Recognise spreadsheets by their columns. Documents are recognised by their filename first, and their contents confirm it (M3).** If the key in the filename and the key in the document differ, that's a finding. Unknown files are reported, never dropped.
- **Link methods are honest:** `exact` when every key matches, `po_level` when only PO and material match (the shipment feed has no lines).
- **Several versions of one thing are all kept** (`entity` + `same_entity` links). The linker names their differences, and the judge decides which to believe.
- **Nothing hardcoded.** Supplier names are learned from the profiles; mappings come from the data.
- **Rebuild everything on each run.** It takes seconds and is always consistent.
- **Unlinked records are findings.**
- **Exact links are certain. Fuzzy links are marked and need review.**
- **The linker lines evidence up but doesn't judge it.** Comparing values is the judge's job.
