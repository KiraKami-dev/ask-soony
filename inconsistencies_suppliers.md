# Inconsistencies in the supplier data

This file covers `suppliers/`: one folder per supplier, each with a profile, a component list, contracts, notices and order confirmations. Soony's own data is in `inconsistencies_soony.md` (IDs S1…).

**Status:** *confirmed* means two or more sources disagree and we checked both. *To verify* means it looks wrong but needs one more check.

## Cross-supplier (profiles and component lists)

| # | Finding | Sources | Impact | Status |
|---|---|---|---|---|
| P1 | **Magnets come fitted in the Collector's box, but Soony's bill of materials lists them as a separate part.** PackRight's component list says CMP-MAG-005 is "fitted by supplier into CMP-GAM-007; not ordered, received or stocked separately" (price 0, no minimum order). Soony's BOM requires 4 per Collector's Edition, so stock 0 shows up as a 100,800 shortage. | `SUP-0029_Guangzhou_PackRight/components.csv`, `soony/master/bom.csv`, `soony/context/launch_plan_GH6.xlsx` (Parts: "CHECK") | **The magnet shortage is a false alarm** (resolves S3). The real magnet risk is the box itself: only about 2,300 spare. | confirmed |

## SUP-0010 Hwaseong Memory (KR, game cards, main source at 70%)

| # | Finding | Sources | Impact | Status |
|---|---|---|---|---|
| P2 | **Short shipment on the launch-critical air freight.** The shipping advice dated 21 Nov says only **28,000 of 48,000** cards flew on AWB 020-48113256, because the carrier offloaded 2 of 7 air-freight containers. The 20,000 balance flies Mon 23 Nov and reaches Hamburg **Tue 24 Nov, after launch**. The shipment feed and the launch plan still show 48,000 arriving 21 Nov. | `order_confirmations/Shipping_advice_020-48113256_4500318735.pdf`, `soony/erp/open_shipments.csv` (SHP-2026-04311), `soony/context/launch_plan_GH6.xlsx` (Inbound) | Game cards: stock 118,400 + 28,000 = 146,400 against 153,200 needed, so **about 6,800 short** across both editions (updates S4 and S5). Boise's 36,000 (ETA 23 Nov) is also too late. This file appeared on 21 Nov, during the hack. | confirmed |

## SUP-0028 Karton Nowak (PL, sleeves, art book, poster, leaflet; single source for art book, poster, leaflet)

| # | Finding | Sources | Impact | Status |
|---|---|---|---|---|
| P3 | **Two suppliers print the GH6 sleeve to different artwork.** All 10 Karton Nowak confirmations with GH6 sleeves say "printed to artwork **rev. C** (GH6_sleeve_revC_USK2026.pdf), received from Soony Marketing 2026-08-07". All 5 PackRight confirmations say "**rev. B**, per Soony drawing on file". Soony's master data says rev **B**. The contract (§ 6.2) says only the artwork last released by Soony in writing is accepted. | `order_confirmations/*` (Karton and PackRight), `soony/master/components.csv` (drawing_rev B), `contracts/SUP-0028_Supply_Agreement_2021.pdf` § 6.2 | Sleeve stock is 122,000 rev. C (Karton) + 64,000 rev. B (PackRight). If only rev. C is valid: 142,000 incl. today's truck against 128,000, so fine. **If only rev. B is valid: 64,000, about 64,000 short.** Either way, two designs end up on shelves. | confirmed (which revision is released? → questions.md) |
| P4 | **Downtime this weekend, notified 9 Nov, not reflected anywhere.** The offset line and the hardcover bindery are down **Sat 21 Nov 14:00 to Mon 23 Nov 06:00**. Only the digital line runs: at most 15,000 sleeves or leaflets in 36 h. Shipping finished goods from their warehouse still works. | `notices/Zawiadomienie_przestoj_techniczny_2026-11.pdf` (Polish) | No reprints of art books or posters until after launch. Notice is in Polish; nobody in procurement reads Polish. | confirmed |
| P5 | **Today's rush offer contradicts the downtime notice.** The offer of 21 Nov (valid 24 h) promises up to **50,000** printed parts in 36 h (contract § 3.2, +35 %). The downtime notice limits rush orders during the downtime to **15,000** on the digital line. Offset rush orders were only accepted until 21 Nov 09:00. | `notices/Oferta_tryb_ekspresowy_2026-11-21.pdf`, `notices/Zawiadomienie_przestoj_techniczny_2026-11.pdf` | Rush printing can't help the launch anyway: 36 h print + about 9 h truck lands after Sunday 20:00. | confirmed |
| P6 | **Karton Nowak has 8,000 art books in stock, ready to ship.** The same offer lists 8,000 art books (production overrun) at the normal price, loaded within 4 h of the order, plus about 9 h by truck to Hamburg. | `notices/Oferta_tryb_ekspresowy_2026-11-21.pdf` | **Fixes S1 (art books about 5,900 short).** Value 8,000 × €2.10 = €16,800, so a buyer can approve it (PP-07: under €50k). Needs a dock slot from Katrin Jansen. Offer expires 22 Nov. | confirmed (opportunity) |
| P7 | **Open orders without a confirmation.** PO 4500318782 (GH5 sleeves 70,000, art books 22,500, leaflets 760,000) has no confirmation at all, yet a truck is booked for its GH5 sleeves (SHP-2026-04316). The art book line of PO 4500318775 (24,500) is also unconfirmed. | `order_confirmations/`, `soony/erp/purchase_orders_2025H2-2026.csv`, `soony/erp/open_shipments.csv` | Not launch-critical (delivery after launch or GH5 only), but the promised dates are not backed by the supplier. | confirmed |
| – | Contract Annex A (prices, minimum order quantities, lead times) matches `components.csv`. | `contracts/`, `components.csv` | – | consistent |
