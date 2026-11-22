# Strategy

## The problem

Soony's systems drift away from the paper trail, and nobody notices until the dock. Almost every launch-critical finding follows the same pattern: the number in SAP or a spreadsheet is wrong, and a document already said so.

- Art books: SAP says 80,000, the delivery note says 8,000 (handwritten).
- Statues: stock says usable, the inspection report says the lot failed.
- Game cards: the shipment feed says 48,000, the shipping advice says 28,000.
- Karton: no capacity limit anywhere, but a Polish notice from 9 Nov says the plant is down all weekend.

The truth was in an unread scan, a foreign-language PDF or a confirmation remark. The opposite also happens: magnets look 100,800 short, but the contract annex says they come fitted in the box.

This happens at every company with an ERP and outside suppliers, on every order. A launch only makes the cost visible.

## What Soony told us

Nils Berger (IT & Systems Lead), Saturday:
1. His dream is an ERP that is clean and 100 % trustworthy.
2. He wants less human error. Receipts and other bookings are typed in by hand.

Our answer is one engine pointed two ways:
- **Detect:** find what is already wrong in SAP (step 1, the verifier).
- **Prevent:** read the delivery note and fill in the booking, so a person only confirms. The S1 error (80,000 typed where the note says 8,000) can't happen.

Prevention comes almost free once step 1 works, because the verifier already pulls out every field a receipt needs.

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

Add more types only once these three work on new files without code changes.

**Then: prevent.** Prefilled goods receipts from delivery-note scans, confirmed by a person. This is also the agent's first real action, which the jury asks for ("do agents act or only summarize?").

Steps 2 to 4 (price, route, close the loop) come after that.

## Pitch line

"Soony's numbers are claims. We check them against the paper before the dock does, and once a booking matches the paper, nobody has to type it again."
