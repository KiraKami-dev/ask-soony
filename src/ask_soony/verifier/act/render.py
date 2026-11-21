"""One screen: the decision cards as a single self-contained HTML page."""

import html
import json
from urllib.parse import quote

from ask_soony.verifier.act.cards import Card
from ask_soony.verifier.impact.shortfall import Shortfall
from ask_soony.verifier.impact.timing import fmt

CSS = """
:root { --bg:#f6f7f9; --card:#fff; --ink:#14161a; --muted:#5d6470; --line:#e3e6ea;
  --risk:#c2410c; --risk-bg:#fff4ed; --ok:#15803d; --accent:#1d4ed8; --chip:#eef1f5; }
@media (prefers-color-scheme: dark) { :root { --bg:#0f1115; --card:#171a20; --ink:#e8eaee;
  --muted:#9aa1ad; --line:#272b33; --risk:#fb923c; --risk-bg:#2a1a10; --ok:#4ade80;
  --accent:#7aa2ff; --chip:#222630; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, sans-serif; }
main { max-width:980px; margin:0 auto; padding:32px 16px 64px; }
h1 { font-size:28px; margin:0 0 4px; letter-spacing:-.01em; }
.sub { color:var(--muted); margin:0 0 24px; }
.summary { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px; margin-bottom:28px; }
.tile { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }
.tile .k { color:var(--muted); font-size:13px; }
.tile .v { font-size:26px; font-weight:650; font-variant-numeric:tabular-nums; }
.tile .v.risk { color:var(--risk); } .tile .v.ok { color:var(--ok); }
.card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:18px 20px; margin-bottom:14px; }
.head { display:flex; gap:16px; align-items:flex-start; }
.num { min-width:110px; background:var(--risk-bg); color:var(--risk); border-radius:10px; padding:8px 10px; text-align:center; }
.num.zero { background:var(--chip); color:var(--muted); }
.num b { display:block; font-size:22px; font-variant-numeric:tabular-nums; }
.num span { font-size:12px; display:block; }
.num .after { color:var(--ok); margin-top:4px; font-weight:600; }
.tile .after { color:var(--ok); margin-top:4px; }
.due { display:inline-block; background:var(--risk); color:#fff; border-radius:999px; padding:1px 10px; font-size:12px; font-weight:650; margin-right:6px; }
h2 { font-size:17px; margin:2px 0 6px; }
.chip { display:inline-block; background:var(--chip); color:var(--muted); border-radius:999px; padding:1px 9px; font-size:12px; margin-right:6px; }
.vs { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin:14px 0 10px; }
.vs div { border:1px solid var(--line); border-radius:10px; padding:10px 12px; font-size:14px; }
.vs .label { display:block; font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; margin-bottom:2px; }
.decision { border-left:3px solid var(--risk); padding:6px 12px; margin:10px 0; font-weight:550; }
ul.opts { margin:6px 0 10px; padding-left:18px; color:var(--ink); }
.actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }
.btn { display:inline-flex; gap:6px; align-items:center; border:1px solid var(--line); background:var(--card);
  color:var(--ink); border-radius:9px; padding:7px 12px; font:inherit; font-size:14px; text-decoration:none; cursor:pointer; }
.btn.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
details { margin-top:10px; color:var(--muted); font-size:13px; }
details pre { white-space:pre-wrap; background:var(--chip); border-radius:8px; padding:10px; color:var(--ink); }
@media (max-width:640px) { .vs { grid-template-columns:1fr; } .head { flex-direction:column; } }
"""


def _e(text) -> str:
    return html.escape(str(text or ""))


def _mailto(message) -> str:
    return (
        f"mailto:{quote(message.to_email or '')}?subject={quote(message.subject)}"
        f"&body={quote(message.body)}"
    )


def _card(card: Card, index: int) -> str:
    risk = card.at_risk
    if card.blocked_only:
        num = '<div class="num zero"><b>No gap</b><span>stock blocked</span></div>'
    else:
        after = (
            f'<span class="after">{card.at_risk_if_acted:,.0f} if you act in time</span>'
            if card.at_risk_if_acted != risk
            else '<span class="after">no fix in time</span>'
        )
        num = f'<div class="num"><b>{risk:,.0f}</b><span>copies at risk</span>{after}</div>'
    timing = ""
    if card.act_by:
        timing += f'<span class="due">Act by {_e(card.act_by)}</span>'
    if card.runs_dry:
        timing += f'<span class="chip">Line runs out {_e(card.runs_dry)}</span>'
    options = "".join(f"<li>{_e(o)}</li>" for o in card.options)
    decision = f'<div class="decision">Decide: {_e(card.decision)}</div>' if card.decision else ""
    actions, drafts = [], []
    for i, m in enumerate(card.messages):
        label = (
            f"Email {m.to_role.split(' (')[0]}" if m.to_email else f"Ask {m.to_role.split(' (')[0]}"
        )
        cls = "btn primary" if i == 0 else "btn"
        actions.append(f'<a class="{cls}" href="{_e(_mailto(m))}">✉ {_e(label)}</a>')
        actions.append(f'<button class="btn" data-copy="c{index}m{i}">Copy text</button>')
        drafts.append(
            f"<p><b>To:</b> {_e(m.to_role)} {_e(m.to_email or '')}<br><b>Subject:</b> {_e(m.subject)}</p>"
            f'<pre id="c{index}m{i}">{_e(m.body)}</pre>'
        )
    evidence = "".join(f"<li>{_e(x)}</li>" for x in card.evidence)
    return f"""
<section class="card">
  <div class="head">{num}<div>
    <h2>{_e(card.title)}</h2>
    {timing}<span class="chip">{_e(card.edition)}</span><span class="chip">{_e(card.part)}</span>
  </div></div>
  <div class="vs"><div><span class="label">System says</span>{_e(card.system_says)}</div>
  <div><span class="label">Paper says</span>{_e(card.paper_says)}</div></div>
  {decision}
  {f'<ul class="opts">{options}</ul>' if options else ""}
  <div class="actions">{"".join(actions)}</div>
  <details><summary>Messages and evidence</summary>{"".join(drafts)}<ul>{evidence}</ul></details>
</section>"""


def render(result: Shortfall, cards: list[Card], built_at: str) -> str:
    tiles = []
    for e in result.editions:
        at_risk, part = result.at_risk(e.product)
        after, _ = result.at_risk(e.product, acted=True)
        cls = "risk" if at_risk else "ok"
        tiles.append(
            f'<div class="tile"><div class="k">{_e(e.name or e.product)}</div>'
            f'<div class="v {cls}">{at_risk:,.0f}</div>'
            f'<div class="k">at risk of {e.remaining:,.0f} still to pack'
            f"{f' · binding part {_e(part)}' if part else ''}</div>"
            f'<div class="k after">{after:,.0f} if you act in time</div></div>'
        )
    for p in result.shared_gaps:
        tiles.append(
            f'<div class="tile"><div class="k">{_e(p.description or p.part)} (shared)</div>'
            f'<div class="v risk">{p.gap:,.0f}</div><div class="k">short across editions</div>'
            f'<div class="k after">{p.gap_if_acted:,.0f} if you act: nothing arrives in time</div>'
            "</div>"
        )
    now = fmt(result.now) if result.now else built_at
    hours = f", {result.hours_left:.1f} packing hours left" if result.hours_left else ""
    plan_ok = sum(1 for p in result.parts if (p.plan_status or "").lower() == "ok")
    short = sum(1 for p in result.parts if p.gap)
    cards_html = "".join(_card(c, i) for i, c in enumerate(cards))
    script = """
document.addEventListener('click', e => {
  const id = e.target.dataset && e.target.dataset.copy; if (!id) return;
  navigator.clipboard.writeText(document.getElementById(id).textContent);
  e.target.textContent = 'Copied'; setTimeout(() => e.target.textContent = 'Copy text', 1500);
});"""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Launch Gut Check</title><style>{CSS}</style></head><body><main>
<h1>Will we make the launch?</h1>
<p class="sub">The launch plan says {plan_ok} of {len(result.parts)} parts are ok. The paperwork says
{short} are short. As of {_e(now)}{_e(hours)}; built from the portal data and the documents.</p>
<div class="summary">{"".join(tiles)}</div>
{cards_html}
<script>{script}</script></main></body></html>"""


def to_json(result: Shortfall, cards: list[Card]) -> str:
    from dataclasses import asdict

    return json.dumps(
        {
            "editions": [
                asdict(e) | {"remaining": e.remaining, "at_risk": result.at_risk(e.product)[0]}
                for e in result.editions
            ],
            "parts": [asdict(p) | {"usable": p.usable, "gap": p.gap} for p in result.parts],
            "cards": [asdict(c) for c in cards],
        },
        indent=2,
        ensure_ascii=False,
        default=lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v),  # datetimes
    )
