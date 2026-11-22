# Interview: Sophia, senior buyer (external)

Sun 22 Nov 2026, morning. External senior buyer we reached out to; not part of the Soony personas. She agreed that we can name her in the pitch and say she liked the product.

## Key insights

- **Confirmation and tracking are the manual pain.** Tracking runs on a spreadsheet at her company too ("it's horrible"); automating it "alone would solve so many delivery issues".
- **Tracking = two Excel files side by side.** She exports the order list (article, quantity, price, confirmed yes/no, requested vs confirmed delivery date) and asks the supplier for a shipped-goods Excel, then compares by hand. Nothing flows back into SAP.
- **SAP gives no warning.** It only updates when the warehouse scans the goods; if the date passes and nothing arrives, the order just shows as cancelled ("Storno").
- **Small discrepancies get deliveries rejected.** A packing-slip text mismatch, or a slip outside the box instead of inside, means logistics refuses the delivery.
- **Suppliers can push back before confirming.** Her order tool (name uncertain, "Oscar") lets suppliers flag wrong quantities, prices or dates between order and confirmation.
- **Prevention beats recovery.** Riots or floods on the route can't really be fixed; the value is catching mistakes (80,000 vs 8,000) before shipping, because once it fails both sides lose money and the relationship.
- **Reorders are a missed step.** If 80,000 arrive and 70,000 sell on day one, suggest how many to reorder.
- **UI: prefers the decision portal over the chat pipeline**, because she doesn't want to talk to an agent; she wants the drafted email and one click to send. Her ideal is a mix: "what needs my attention" list plus the pipeline context, and on clicking an item, ready-made options ("Shall I draft an email? Or something else?"), like Amazon Quick.

## Transcript

Cleaned transcript. Filler and false starts removed and mistranscriptions corrected. [unclear] marks passages where the audio could not be recovered.

Speakers: TEAM (interviewers, hackathon team), SOPHIA (senior buyer).

---

**TEAM:** Hi, how are you?

**SOPHIA:** Good, how are you?

**TEAM:** Very sleepy. We worked through the night. My first thought this morning was, "I know someone who works in procurement," so now was the time to call.

**SOPHIA:** Good call. Glad you made the time on a Sunday.

**TEAM:** We won't take much of your time. Here's a quick brief of the challenge.

Imagine a company like Sony is about to launch a game, a mock GTA 5. They want to get about 640,000 copies of the standard edition onto the market (there's a collector's edition too, but this one makes more sense to walk through). Some of the products still have to be procured, so the order is in progress.

In the current flow, only some steps (the purple ones on the slide) are recorded in SAP. Everything else is manual. Our job is to make sure the data stays consistent through the whole process after an order is placed, and the end goal is that every product arrives in time for the game to ship.

We'd like your perspective as a senior buyer. Which of these steps do you handle? Would you say you handle the whole order?

**SOPHIA:** Honestly, it's pretty much what you have on the slide. I enter the order into the system. Confirmation is mainly handled by other departments, but things tend to slip, so it ends up being a manual process. If that were automated, procurement would be so much easier.

Then there's tracking. Is that manual for you as well?

**TEAM:** Yes. They just update a spreadsheet.

**SOPHIA:** For us too, and it's horrible. It should be automated, because that alone would solve so many delivery issues.

After that come the packing slips. They usually come from the warehouse where the goods are produced, not from the company directly, and they go to the logistics system. If there's even a small discrepancy in the text, logistics doesn't accept the delivery and sends it back.

It also depends on company policy and what's agreed in the contract. At my company, if a delivery isn't what it should be, the goods are returned or not accepted. The same goes for packaging that doesn't match what we agreed, for example a packing slip outside the box instead of inside.

**TEAM:** So any anomaly.

**SOPHIA:** Exactly. I don't know if that's relevant for you, but automating the confirmation and tracking process would be a huge help.

**TEAM:** That's exactly what we're aiming to tackle. One question: there's a sheet exported from SAP called "Open Shipments" that tracks all shipments. Is that the same sheet the buyer uses as their tracker, or do they keep their own document of what's happening with each shipment?

**SOPHIA:** When we want to track orders, we go into the order system, the tool where I enter the orders, and download an Excel file. It has everything: article number, colour code, article description, quantity ordered, price, whether the order is confirmed (yes/no), the estimated delivery date and the confirmed delivery date, because the supplier can confirm or change the delivery date you asked for. It's a huge Excel file with every piece of information you need.

To track the order, I ask the supplier for an Excel file showing what has already been shipped. Then we put the two sheets side by side and check manually which goods have arrived.

**TEAM:** Follow-up question: when you update that sheet, does it also update in SAP?

**SOPHIA:** No, and that's the problem. Every single time you have to take a fresh export and ask the supplier for a new spreadsheet.

**TEAM:** So how does tracking get back into SAP?

**SOPHIA:** It doesn't, at least not for me. SAP only updates the moment the goods are delivered: the logistics centre scans the product and it shows as arrived. If the delivery date passes and the goods never reach the warehouse, SAP just says "cancelled" or "Storno" or something similar. You don't get any kind of warning.

**TEAM:** Makes sense. One last question about the flow. After tracking, is there a receiving step where you confirm what you received? Say an order of 80,000 was mistakenly entered as 8,000. At receiving, would you correct it there and feed that back into SAP?

**SOPHIA:** When we place an order, the system is connected to another tool. In my case it's called Oscar [name uncertain]. It's a communication tool between the company and the supplier. Anything I enter can contain a mistake, right? If I enter 80,000 copies but the supplier can only provide 8,000, the supplier can flag it in the tool and say, "No, 80,000 is wrong, we agreed on 8,000." They can do the same for prices, estimated delivery dates, anything.

So between step one and step two, order and confirmation, the supplier can comment on the order and request changes before confirming. Does that answer your question?

**TEAM:** Yes. Is there also a comment field? In our data, the spreadsheet has a comment column for updates on each shipment.

**SOPHIA:** It's an Excel file, so I can add any column I want, including a comment column. Every company probably works a bit differently, with different order tools and formats, but it's more or less the same information everywhere.

### Prototype 1: decision portal

**TEAM:** Now the interesting part. We built two solutions that technically do the same thing, and we'd love to know which one you'd prefer to use.

You said the problems usually happen at confirmation and at tracking. For that, we built a portal that re-verifies everything. Say you ordered 80,000 and the supplier misread it and entered 70,000, or didn't enter it at all. We can't change the supplier's side; they have their old system. So as soon as the supplier's confirmation comes back, we cross-check it. If something is wrong, the portal asks you to make an immediate decision so the process doesn't stall.

**SOPHIA:** Just to clarify: I enter 80,000, the supplier confirms but only puts 8,000, and your system catches that?

**TEAM:** Correct. The portal flags that the supplier made a mistake. SAP is the source of truth, so you can say, "This is a mistake, it should be 80,000," and the portal learns from your decisions over time. It walks you through the data in SAP so you can verify early. For example: "You ordered X, but the supplier's shipping advice says 20,000 arriving Tuesday." Once you approve, it emails the supplier on your behalf, the corrected value becomes the source of truth, and the process continues.

For shipment tracking, we monitor ongoing news and learn from past supply issues to predict risks. Say you're launching "Grand Heist" tomorrow, but there are riots or a tornado on the route. We warn you that you'll be short this many units and give you alternatives, such as sending an urgent request to the supplier. An agent automates that in the background, so you get decision options and don't lose the money or the customer you planned for. And it keeps learning.

**SOPHIA:** That's a cool tool, definitely.

**TEAM:** Lastly, we run a three-way match. You're probably familiar with it: order, confirmation and what was actually received. That confirms the right order was shipped throughout the process. If it doesn't match, we trace where it went wrong and our agents email the shipping companies about the losses. If 10,000 units didn't arrive, the agent follows up automatically so the shipment gets moving again.

**SOPHIA:** That's super helpful too, but I'd say the most effective thing is to identify the problem before it happens. If there are riots or floods and the goods don't reach your warehouse, there's not much you can do. That happens these days, and there's no real solution.

The best approach is to find a solution between the supplier and the producer before the problem arises. Catching the 80,000 vs 8,000 mistake is great, and so is giving the company a chance to make changes during the order. But once a problem has happened, both companies lose money. You lose a customer, you lose income, and the supplier relationship is damaged too.

**TEAM:** So we need to prevent it before it even gets to shipping.

**SOPHIA:** Exactly. A preventative tool is the best thing you can build, one that flags mistakes before they happen.

**TEAM:** Would you like a portal like this that tells you what's critical so you can act on it?

**SOPHIA:** That would be great, because right now we really do it all manually. I have an assistant and I tell them, "We need to track this." I work with so many brands and products that it takes up a lot of time.

Something else that would be really interesting is reorders. Say the 80,000 copies arrive and you sell 70,000 in one day. The tool could calculate the potential extra sales from a reorder and suggest how many copies to reorder. That benefits both the supplier and the company.

**TEAM:** We never thought beyond that point. Once delivery was complete, we considered it done. That's a really good point. We'll see if we can implement it.

### Prototype 2: pipeline view with chat agent

[Screen share. Initially blurry, later readable.]

**TEAM:** This is the same process as the first prototype, but built from templates. For Sony we have a template with SAP and the whole process you just saw. Another company might have an extra step in the middle, like your supplier communication tool that rejects an order if the supplier doesn't have the product. To scale to other companies, you describe your pipeline to an AI and it generates the complete pipeline for you. Inside your company, whenever a new order is placed, the pipeline keeps updating.

Behind the scenes it does the same things: it triggers an email when something goes wrong, predicts risks and runs the three-way match to keep the data consistent.

So which portal would you prefer, visually and in terms of making decisions faster?

**SOPHIA:** Can I click on this?

**TEAM:** You can click anything. This step, "waiting for data," means it's waiting for updates. In some scenarios a step is halted with an error because a decision is needed, and until then the pipeline is stopped. On the right is a chat where you can tell the agent, "Email the supplier and solve it."

**SOPHIA:** They're both good, but I think I prefer the first one, because you don't have to actively talk to an agent. The dashboard shows you each item and you just say, "OK, send the email." It gives you the solution, which makes it easier to navigate. What do you think?

**TEAM:** I like it too, but it shows less information. I'm torn between how much data to show a senior buyer. Should we show the pipeline visually so they can click a halted step and decide what to do? Or just notify them that something went wrong and they need to decide based on criticality?

**SOPHIA:** Can you show me the first one again?

**TEAM:** Sure. This one shows "What needs your decision," and it has already drafted an email explaining what happened, e.g. "We need this many game cards before this date. Can you deliver from stock?" You review it, click send, and you're done.

**SOPHIA:** Is a combination of both possible? Have you seen Amazon Quick, the AI tool from Amazon? It gives you ready-made options, a bit like a combination of your two tools. It says, "You could do this or this. Shall I draft an email? Or would you like to do something else?"

So: all the information on hand like in the second tool. "What needs my attention" is perfect. Then, as soon as you click an item, it offers ready-made options for what to do. I think that would be awesome, a mix of both.

It's really nice to see what needs attention across the whole pipeline, and to have all the information you need as a buyer. But some things can simply be automated: click "send email," finalise it, done. A combination of both would be great.

**TEAM:** Good point. We can keep the action panel on the left and the pipeline always visible. We'll start working on it right away. We have a couple of hours left.

**TEAM:** One more thing. Is it OK if we mention in our pitch that we interviewed Sophia from your company, that she gave us insights and liked the product?

**SOPHIA:** Yes, sure.

**TEAM:** Awesome. Thank you so much for your time. Let's connect again. Have a nice day!

**SOPHIA:** Bye!

[End of interview]
