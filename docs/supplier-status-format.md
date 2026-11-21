# Supplier status format v1

A plain text block that suppliers put into the body of an email whenever something changes on an order: a confirmation, a date or quantity change, a hold or release, a dispatch. Our watcher reads it from the inbox, so status no longer has to be pieced together from PDFs and free text. Suppliers can type it by hand or export it from their system; it needs no portal and no integration project.

## Order lines

One line per PO item, fields separated by semicolons, header line first:

```
#SOONY-STATUS v1
PO;ITEM;SOONY_PART;SUPPLIER_PART;REV;QTY_ORDERED;QTY_CONFIRMED;QTY_SHIPPED;UNIT_PRICE;CURRENCY;INCOTERM;CONFIRMED_DATE;STATUS;HOLD_REASON;MODE;AWB_OR_CONTAINER;DELIVERY_NOTE;ETD;ETA;NOTE
4500000001;00010;CMP-XYZ-001;SUP-PN-17;C;10000;10000;6000;1.25;USD;EXW Example City;2026-01-15;PARTIAL;;AIR;000-12345678;DN-260110-001;2026-01-10;2026-01-12;balance of 4000 on next flight
4500000001;00020;CMP-XYZ-002;;;5000;5000;0;0.80;USD;EXW Example City;2026-01-20;ON_HOLD;INCOTERM_CONFIRMATION;;;;;;awaiting written confirmation
#END
```

Required: `PO`, `ITEM`, `SOONY_PART`, `QTY_CONFIRMED`, `CONFIRMED_DATE`, `STATUS`. All other fields when known.

| Field | Why it is there |
|---|---|
| `QTY_CONFIRMED`, `QTY_SHIPPED` | Partial shipments show up as numbers, not in a sentence |
| `STATUS`, `HOLD_REASON` | A hold at the supplier is visible even when a booking exists |
| `INCOTERM`, `UNIT_PRICE`, `CURRENCY` | Term and price mismatches with our PO surface on the first message |
| `SUPPLIER_PART` | Suppliers using their own part numbers map to ours on every line |
| `REV` | Artwork or drawing revision, so superseded revisions are caught before packing |
| `DELIVERY_NOTE` | Lets receiving match a booked quantity to the paper delivery note |
| `MODE`, `AWB_OR_CONTAINER`, `ETD`, `ETA` | Shipment status per order line instead of a manually kept tracker |

`STATUS`: `CONFIRMED`, `CHANGED`, `PACKED`, `ON_HOLD`, `RELEASED`, `SHIPPED`, `PARTIAL`, `DELIVERED`, `CANCELLED`

`HOLD_REASON`: `INCOTERM_CONFIRMATION`, `QUALITY`, `CAPACITY`, `EXPORT_LICENCE`, `PAYMENT`, `OTHER`

## Notices

Events that are not tied to one PO item go into the same block:

```
#NOTICE;TYPE;EFFECTIVE_FROM;EFFECTIVE_TO;AFFECTED_PARTS;TEXT
#NOTICE;CAPACITY;2026-01-21T14:00+01:00;2026-01-23T06:00+01:00;CMP-XYZ-008;line maintenance, finished goods can still ship
```

`TYPE`: `CAPACITY`, `EXPORT`, `PRICE`, `ARTWORK`, `QUALITY`

## Conventions

- Dates as `YYYY-MM-DD`, times with time zone offset
- Quantities without thousand separators; decimal point for prices
- Bank detail changes are never accepted through this format: only in writing plus a confirming phone call
