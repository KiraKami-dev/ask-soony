Note for Farhan: change to the shortfall calculation (Sun 22 Nov, early morning)

Hi Farhan, while you were asleep we changed one calculation in impact/shortfall.py. Nothing is
committed or pushed; it is all in the working tree for you to review.

What was wrong
- PartNeed.gap was needed - usable stock. It ignored supply that is already on its way and
  lands in Hamburg before packing ends (Sun 20:00).
- Example: the game card card said 34,800 short (153,200 needed - 118,400 book stock as of
  20 Nov). Hwaseong's air freight 020-48113256 (PO 4500318735) lands on 21 Nov; its shipping
  advice says 28,000 of 48,000 were flown, the 20,000 balance lands Tue 24 Nov (after launch).
  The real gap is 6,800, which is also what papercheck.py rule a computes.

What changed
- shortfall.py: new PartNeed.arriving and PartNeed.not_counted lists, new property
  arriving_in_time, and gap = needed - usable - arriving_in_time. gap_if_acted, at_risk and
  the cards follow automatically.
- New function _arriving() fills both lists. Sources: plan_inbound rows, plus ERP shipment rows
  (newest per reference; quantity from their open PO lines when the plan has no row). It counts
  an inbound only if:
  - ETA is on or before launch day (same cut-off as the existing after_launch rule)
  - not cancelled and not "arrived" at the port (that stays a collect fix, not available stock)
  - no goods receipt for its PO and part between its ETD and the stock date (that would already
    be in the book stock); a receipt after the stock date counts with the received quantity
  - not overdue (ETA before the stock date and no receipt: treated as lost, not as supply)
  - the supplier's shipping document for the same reference does not say less; if it does,
    only the shipped quantity counts and the balance goes to not_counted with its expected date
  - if such a shipping document exists but has no reading yet, it is NOT counted (reason
    "not read yet"), because the paper may be a short-shipment advice like this one
- cards.py (allocate card only): paper_says, the decision question and the approver message use
  stock + arriving; evidence lists what was counted as arriving and what was not, and why.
- Tests: 5 new tests in impact/tests/test_shortfall.py (in-time vs after-launch vs cancelled vs
  unread advice; shipped short with balance, card disappears when the gap closes; receipt before
  and after the stock date; overdue shipment). Full suite: 219 passed, ruff clean.

Important: readings
- There is no data/store/image_recognizer on Jonas's Mac, so the shipping advice has no reading
  there. With the rule above the dispatch run there still says 34,800 short, and now names the
  unread advice under "Not counted". Once the advice is read (your readings store, or one
  image_recognizer run with --only Shipping_advice_020-48113256), the same run gives
  6,800 short.
- We checked this with a hand transcription of that one PDF as a temporary reading (outside the
  repo, not committed): 6,800 short, allocate card still there, 20,000 balance not counted.

Not changed, worth a look
- runs_dry still uses usable stock only, so the allocate card's act-by time ignores arrivals.
- impact/__main__.py does not print the arriving list yet (the gap it prints is the new one).
- The plan's due_before_launch column (48,000) is still the system view; we did not use it.
