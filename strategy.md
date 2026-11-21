# Strategy

## The problem

Soony's systems drift away from the paper trail, and nobody notices until the dock. Almost every launch-critical finding follows the same pattern: the number in SAP or a spreadsheet is wrong, and a document already said so.

- Art books: SAP says 80,000, the delivery note says 8,000 (handwritten).
- Statues: stock says usable, the inspection report says the lot failed.
- Game cards: the shipment feed says 48,000, the shipping advice says 28,000.
- Karton: no capacity limit anywhere, but a Polish notice from 9 Nov says the plant is down all weekend.

The truth was in an unread scan, a foreign-language PDF or a confirmation remark. The opposite also happens: magnets look 100,800 short, but the contract annex says they come fitted in the box.

This happens at every company with an ERP and outside suppliers, on every order. A launch only makes the cost visible.

## The product

Every number in the plan is a claim. We check each claim against the documents that confirm or contradict it.

1. **Verify:** read every document and pull out claims (quantity, date, status, revision, price). Line them up with the ERP and the spreadsheets, and show where they disagree with the evidence for both sides. Never pick a side silently.
2. **Price it:** turn each contradiction into copies at risk.
3. **Act:** send the request to whoever can settle it: supplier, Hamburg, or an approver.
4. **Close the loop:** read the reply and update the claim.

## Scope

**We build step 1 first.** It's the hard part: messy files, scans, four languages, and data that changes during the hack.

Start narrow, with three claim types where the evidence is clear:
- **Quantity received:** delivery note vs. goods receipt (S1)
- **Quantity and date shipped:** shipping advice or confirmation vs. shipment feed (P2, P13)
- **Quality status:** inspection report vs. stock status (S2)

Add more types only once these three work on new files without code changes. Steps 2 to 4 come later. A small step 2 is probably needed for the demo, because the jury asks whether the agents act or only summarize.

## Pitch line

"Soony's numbers are claims. We check them against the paper before the dock does."
