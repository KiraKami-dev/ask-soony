# Verifier

Every number in Soony's systems is a claim. The verifier checks each claim against the documents that confirm or contradict it, decides which to believe, and says who to ask when the data can't settle it.

This folder holds the notes and the verifier scripts.

## Pipeline

1. **Link:** connect all records that describe the same order, delivery or lot
2. **Read:** turn each document into claims
3. **Judge:** where claims disagree, decide which to believe
4. **Route:** when the data can't settle it, decide who to ask

## 1. Linking

Before anything can be compared, we need to know which records belong together. This is about half the work.

### The chain of one order

```
PO line → confirmation → shipping document → shipment → delivery note → goods receipt → stock batch → inspection
```

| Record | What it is | Who owns it |
|---|---|---|
| PO (purchase order) | Soony's order: part, quantity, price, date | Soony buyer, in SAP |
| Confirmation | The supplier's reply to a PO: what it promises to deliver, when, at what price | Supplier |
| Shipping document | What the supplier physically sent (shipping advice, packing list) | Supplier |
| Shipment | Soony's record of the transport: container, flight or truck, with ETA and status | Soony logistics (ERP feed; Excel tracker kept by hand) |
| Delivery note | Paper that travels with the goods; Hamburg may correct it by hand | Supplier writes it; Hamburg annotates it |
| Goods receipt | Hamburg's booking of what arrived, typed in by hand | Soony Hamburg, in SAP |
| Stock batch | What SAP believes is in the warehouse, created by the receipt | Soony, in SAP |
| Inspection report | Quality check of a received lot | Soony Hamburg quality |

### Keys between records

| Link | Owners | Key | Where it appears |
|---|---|---|---|
| PO ↔ confirmation | Soony ↔ supplier | PO number | `po_number` in SAP; filename `OA<conf>_<PO>.pdf` |
| PO ↔ shipping document | Soony ↔ supplier | PO number | filename `Shipping_advice_<AWB>_<PO>.pdf`, `PL_<ref>_<PO>.pdf` |
| PO ↔ shipment | Soony ↔ Soony | PO number (no line) | `open_shipments.csv` `po_numbers`, several joined with `\|` |
| Shipment ↔ shipping document | Soony ↔ supplier | Air waybill / container number | `open_shipments.csv` `reference`; inside the document |
| Shipment ↔ tracker | Soony ERP ↔ Soony Excel | Shipment id | `shipment_id`; the tracker uses supplier names |
| PO line ↔ goods receipt | Soony ↔ Soony Hamburg | PO number + line | `goods_receipts.csv` `po_number`, `po_line` |
| Goods receipt ↔ delivery note scan | Soony Hamburg ↔ supplier (+ Hamburg's handwriting) | Delivery note number | `goods_receipts.csv` `delivery_note`; filename `<date>_<WZ\|SN\|DN>-….jpg/png` |
| Goods receipt ↔ stock | Soony ↔ Soony | Batch = receipt number | `gr_number` = `stock_snapshot.csv` `batch` |
| Batch ↔ inspection | Soony ↔ Soony Hamburg quality | Batch number | filename `inspection_report_<batch>.pdf` |
| Anything ↔ part | Soony master ↔ supplier numbering | Material id | `CMP-…`; PackRight documents use `PR-…` (contract Annex C) |
| Anything ↔ supplier | Soony (several formats) | Supplier id | `SUP-0019`; legacy PO file uses `19`; tracker uses names |

### Normalise before linking

- **Legacy PO export:** SAP field names (`EBELN` = PO, `EBELP` = line, `LIFNR` = supplier, `MATNR` = material), `;` separator, decimal commas, `MM/DD/YYYY` dates. Line `00010` probably means line 1 (to verify).
- **Supplier:** numeric id, `SUP-` id, folder name and display name all map to one id.
- **Part:** supplier part numbers map to `CMP-` ids.
- **Cancellations:** negative quantities.

### Unlinked records are findings

A record that links to nothing is reported, not dropped. Examples: a receipt with no PO (the pre-order pins), a PO line with no confirmation, a scan with no receipt, a shipment line with no confirmation.

Links have a confidence level: an **exact key** match is certain. A **fuzzy** match (same supplier, part, date range and quantity) needs review.

## 2. What can disagree

Five things:

1. **Quantity:** how many were ordered, shipped, received or are in stock
2. **Date:** when something ships or arrives
3. **Status:** usable or blocked, approved or conditional, in transit or arrived
4. **Revision:** which version of a part (e.g. sleeve artwork rev. B vs. rev. C)
5. **Price:** what Soony pays vs. what the contract allows

## 3. Who would know: evidence by fact

| Fact | Who would know | Example |
|---|---|---|
| What was ordered | SAP purchase order | PO for 8,000 art books |
| What was shipped | The supplier's own shipping document | Shipping advice: 28,000 of 48,000 flew |
| What arrived and in what condition | Physical evidence at Hamburg (delivery note, inspection report) | Handwritten 8,000; 9 of 125 statues failed |
| Price and terms | The contract | Boise ships EXW; prices fixed for the year |
| Anything else | Spreadsheets (lowest weight) | The shipment tracker |

### When they disagree

Start with the source that would know, then apply three checks:

- **Newer beats older** for the same event. The shipping advice from 21 Nov beats the shipment feed.
- **A second independent source settles it.** The PO says 8,000, which backs the handwritten 8,000 against SAP's 80,000.
- **Typical error patterns raise suspicion:** a factor of 10, swapped digits, wrong units.

| Outcome | Meaning | What happens |
|---|---|---|
| **Resolved** | The evidence agrees | Propose the fix |
| **Likely** | Strong evidence, but not conclusive | A person confirms |
| **Can't tell** | The data can't settle it | Ask whoever would know |

## 4. Who to ask

| Question | Ask | Example |
|---|---|---|
| What arrived? | **Hamburg** (count or check it) | Is it 8,000 or 80,000 art books? |
| What shipped? | **The supplier** | What is on the plane? |
| What is allowed? | **An approver** from policy PP-07 (vacant role → Head of Procurement) | May the failed statue lot be used? |
| Which version is valid? | **Marketing** | Sleeve rev. B or rev. C? |

Contact suppliers only about contradictions that matter. They stop replying when spammed.

## Building blocks

- **Claim store:** one shared table of claims, recording what, value, source, issuer and time. Every step reads from it and writes to it.
- **Linker:** builds the chain above, exact keys first, fuzzy matches flagged.
- **Reader:** turns each new or changed file into claims. Text PDFs go through text extraction. Scans and images go to a multimodal model (no training). Printed and handwritten values are kept separate.
- **Judge:** applies the evidence rules.
- **Router:** picks who to ask and drafts the question.
