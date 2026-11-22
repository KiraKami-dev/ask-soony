# Demo script: 3 minutes on stage, then 2 minutes of questions

Run it on the demo instance (`https://ask-soony-demo.stenglein.dev`, reset with
`scripts/demo.sh reset` before every rehearsal). Supplier email there is only recorded, so
every click is safe. Two devices: the laptop on the projector (Felix) and a phone with push
switched on (Hamburg, and Maren).

## 0:00 to 0:30: the problem (one slide, then straight into the app)

> "Soony's launch is tonight at 20:00. One warehouse, six suppliers, the last 20 % still to pack.
> SAP has the purchase orders, everything else is PDFs, scans and a spreadsheet that is updated
> 'when someone remembers'. Their head of procurement told us: *we find out at the dock*.
> The system says they're fine. It isn't."

Open **Launch** on the laptop.

## 0:30 to 1:00: what nobody saw (Launch)

- Point at the verdict: Standard has stood still since Saturday evening, Collector's stops
  this morning. "The plan says covered. We count otherwise, and every number links to the
  line in the document it comes from."
- Point at "Packing counts are 12 h old": "Even the one system they have is half a day old.
  We say so instead of pretending."

## 1:00 to 2:00: the agents at work (Today)

Story 1, art books (show, decide, act):
1. Open the art book case: "SAP booked 80,000, the delivery note says 8,000." Click the
   highlighted number, the scan opens with the line marked.
2. "For today: Collector's runs out at 11:16." Recommendation: order 8,000 from Karton's stock.
3. It costs money, so it goes to Maren: **Ask Maren to approve**. The phone buzzes, Maren
   approves on the phone, the email goes out (on the demo: recorded, shown in the trail).
4. "On the live system Karton answered in Polish within minutes. Our agent read the reply,
   counted the books as inbound and noticed the next problem: they arrive at 20:33, after the
   trucks. It opened the follow-up itself: ask for a van."

Story 2, ask the floor (when no source knows):
5. Open "Collector's count is 12 h old", approve. The phone gets the count page, take a photo
   of a count sheet, OCR suggests the number, send. Launch switches from projected to counted.

Story 3, every decision teaches the next one:
6. Decide a second rev B sleeve case: "That's the second time. Handle it like this from now
   on?" Show the routine as nodes: When, Only if, Then, and the trust ladder: it starts by
   asking. "Money and plan changes always come to a person."

## 2:00 to 2:40: the business

> "Soony pays because a missing Collector's copy is a fan who paid for a numbered box. Every
> hour the cell waits costs 900 copies. Felix works through his Sunday on Today; Maren sees
> on Launch whether they make it and signs on her phone.
> This is not a launch tool: deadline, bill of materials, suppliers and paperwork is every
> manufacturer's week. Lio has 175 customers with exactly this gap between order and dock."

## 2:40 to 3:00: close

> "We don't add a system. We turn the work people already do, emails, scans, a photo of a
> count sheet, into data, and where data is missing, our agents ask the right person before
> the gap gets expensive."

## Backup and questions

- **If the Wi-Fi fails:** play the demo video; screenshots in the deck.
- **"What happens when the data changes?"** Every portal change runs all checks; a data check
  warns when a file can no longer be read; a drop drill covered notices, bank detail changes,
  renamed columns, news and a frozen shipment list.
- **"What if the agent is wrong?"** Every number is anchored to its source; contradictions are
  shown, not resolved silently; nothing that costs money runs without a person; routines start
  in "Ask me".
- **"Is anything hardcoded?"** No values from the data; products, parts, sites, deadlines,
  approvers and policies are read at runtime, files are recognised by content.
- **What we learned from Soony's team:** fill in after office hours (Felix on who collects the
  container; Maren on who signs what).

## Pitch deck outline (6 slides, support only)

1. **We find out at the dock.** The quote, launch tonight, one warehouse, last 20 %.
2. **The system says we're fine.** The gap: only 2 of 7 steps have a system; the
   contradictions we found (booking vs. delivery note, plan vs. port, stale counts).
3. **Live demo** (no slide content, just the URL).
4. **Notice, show, act, learn.** The loop in one diagram, with the human gate on money.
5. **Why Soony pays, who's next.** Copies per hour, the two personas, every manufacturer.
6. **What we'd build next.** Probabilities instead of single dates, an agent that asks where
   the answer is worth most, supplier-side reporting, the network effect across customers.
