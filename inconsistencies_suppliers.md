# Inconsistencies in the supplier data

Supplier data (`suppliers/`). Soony data: `inconsistencies_soony.md` (S…). All 229 supplier PDFs were read, and every confirmation was checked line by line against SAP.

## Launch-critical

| # | System says | Evidence says | Impact |
|---|---|---|---|
| P2 | Feed and plan: 48,000 Hwaseong game cards arrive 21 Nov (SHP-2026-04311) | `Shipping_advice_020-48113256_4500318735.pdf` (appeared 21 Nov): 28,000 flew, 20,000 arrive Tue 24 Nov | **About 6,800 game cards short** (S4). Late delivery qualifies for LTA §7 damages of about $3.9k/week. |
| P3 | Master data: GH6 sleeve rev. B | Karton confirmations print rev. C ("received from Soony Marketing 2026-08-07"); PackRight prints rev. B | Hamburg holds 122,000 rev. C and 64,000 rev. B sleeves. **If only rev. B is valid: about 64,000 short.** |
| P4 | Nothing (no capacity limit anywhere) | Polish notice dated 9 Nov: Karton's offset printing and bindery are down Sat 14:00 to Mon 06:00 | No reprints before launch. Karton's rush offer (P5) contradicts this notice, and rush printing lands too late anyway. |
| P6 | Nothing | Karton offer of 21 Nov: 8,000 art books from stock, loaded in 4 h, plus about 9 h by truck | **Fixes S1.** €16,800, so a buyer can approve it. Expires 22 Nov. Needs a dock slot. |
| P16 | Profile: Pune `conditional`, release expired 30 Oct | All Pune confirmations claim "approved for serial supply" | Explains why the failed lot was booked as usable (S2) |
| P13 | Feed: Boise SHP-2026-04314 "booked" for 21 Nov. SAP: DDP Hamburg | Confirmation for PO 4500318769: on hold until Soony confirms EXW in writing. The contract says EXW. | 36,000 cards stuck. After launch anyway. Fix: one email. |
| P1 | BOM: magnets are a separate part, stock 0 | PackRight contract Annex A: fitted in the Collector's box | **False alarm** (S3) |

## Confirmations vs. SAP (not launch-critical)

- **P12, P15, P7** Order lines with no supplier confirmation: Hwaseong 5, Boise 7, Karton 2 POs, PackRight 3. Boise's unconfirmed line is on a plane (SHP-2026-04310). Karton's unconfirmed PO has a truck booked (SHP-2026-04316).
- **P10** Hwaseong PO 4500318636: SAP promises 26 May, the confirmation says 9 Jun.

## Prices vs. contract (business case, not launch)

| # | Supplier | Contract says | Billed |
|---|---|---|---|
| P9 | Hwaseong | GH5 card fixed at $5.40 until expiry | Confirmations quote $5.51 to $5.62 (SAP keeps $5.40) |
| P14 | Boise | 60 days' notice for price changes | +4 % and +3 % effective immediately, accepted in SAP |
| P18 | Dongguan | No price-revision clause | +3 % twice in 2026, 30 lines overpaid |
| P22 | PackRight | Prices fixed for the calendar year | +4 % mid-2025 |

- **P8** Supplier `components.csv` files still carry the old prices, so cost calculations from them come out 3 to 7 % too low.

## Structural risks (after launch)

- **P11** Hwaseong (game cards, 70 % share): contract expires 31 Dec. Renewal must be signed by **15 Dec**. Open PO 4500318783 (134,000 cards) is unconfirmed.
- **P19** Dongguan (steel case, sole source): contract ends 28 Feb 2027. The Chinese New Year shutdown schedule is due 30 Nov.
- **P21** PackRight's box magnets come from a rare-earth sub-supplier (Jiangxi RareMag). Export restrictions count as force majeure, which makes it a single point of failure for every future Collector's Edition.
- **P17** Pune orders placed after 30 Oct are blocked, as the policy says. Only the vacant Category Lead can extend the release.

## Other

- **P20** Dongguan shipped 78,000 pre-order pins without a PO (same as S13).
- **P23** PackRight uses its own part numbers. Contract Annex C maps them to Soony's.

## Contract facts

| Supplier | Parts (share) | Incoterm | Expires | Clause to know |
|---|---|---|---|---|
| SUP-0010 Hwaseong (KR) | game cards (70 %) | FOB Busan | 31 Dec 2026 | §7 late delivery: 2 %/week, max 10 % |
| SUP-0011 Boise (US) | game cards (30 %) | EXW Boise | | Delay liability capped at price |
| SUP-0018 Pune (IN) | statue (20 %) | | | §2.2 conditional release only |
| SUP-0019 Dongguan (CN) | steel case (100 %), statue (80 %), clear case (40 %) | FOB Yantian | 28 Feb 2027 | No price revisions allowed |
| SUP-0028 Karton (PL) | sleeve, art book, poster, leaflet | DAP Hamburg | 31 Dec 2027 | §3.2 rush orders; §6.2 prints the artwork Soony last released in writing |
| SUP-0029 PackRight (CN) | Collector's box (100 %), clear case (60 %), sleeve (40 %) | FOB Yantian | 31 Aug 2027 | §8 force majeure covers rare-earth export restrictions |
