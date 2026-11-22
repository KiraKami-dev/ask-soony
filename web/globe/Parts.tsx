import { COLOR, partRisk, type Risk } from "./model";
import type { EditionRow, NetProduct, PartRow } from "./types";

const ICONS: [RegExp, string][] = [
  [/card|media|chip|memory|disc|game/i, "M7 3h8l3 3v15H7zM10 3v4h4V3"],
  [/case|housing|steel|shell/i, "M4 7h16v12H4zM8 7V4h8v3M4 12h16"],
  [/statue|figure|item|model|diecast/i, "M12 3a3 3 0 100 6 3 3 0 000-6zM8 21v-6l-2-4h12l-2 4v6z"],
  [/book|leaflet|manual|poster|map|sleeve|print|paper|cover/i, "M5 4h11a3 3 0 013 3v13H8a3 3 0 01-3-3zM9 8h6M9 12h6"],
];
const BOX = "M12 3l8 4v10l-8 4-8-4V7zM4 7l8 4 8-4M12 11v10";

function icon(text: string) {
  return ICONS.find(([re]) => re.test(text))?.[1] ?? BOX;
}

const tone: Record<Risk, string> = { red: COLOR.red, amber: COLOR.amber, green: COLOR.green };
const num = (n: number) => n.toLocaleString("en");
const hhmm = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString("en", { weekday: "short", hour: "2-digit", minute: "2-digit" }) : null;

export default function Parts({
  editions,
  parts,
  products,
  selected,
  onSelect,
}: {
  editions: EditionRow[];
  parts: PartRow[];
  products: NetProduct[];
  selected: string | null;
  onSelect: (part: string | null) => void;
}) {
  const rows = editions.map((e) => {
    let list = parts.filter((p) => p.editions.includes(e.product));
    if (!list.length) {
      const prod = products.find((p) => p.id === e.product || p.name === e.name);
      list = (prod?.parts ?? []).map((p) => ({
        part: p.part,
        description: p.description,
        editions: [e.product],
        needed: 0,
        usable: 0,
        gap: 0,
        runs_dry: null,
      }));
    }
    return { e, list };
  });
  const widest = Math.max(...rows.map((r) => r.list.length), 0);
  const multi = rows.filter((r) => r.list.length === widest).length === 1 && rows.length > 1;

  return (
    <div className="space-y-4">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">What goes in the box</h2>
      <div className="grid gap-4 xl:grid-cols-2">
        {rows.map(({ e, list }) => {
          const binding = list.length
            ? list.reduce((a, b) => (b.usable - b.needed < a.usable - a.needed ? b : a))
            : null;
          return (
            <section key={e.product} className="rounded-lg border border-slate-800 bg-slate-900/70 p-3">
              <div className="mb-2 flex items-baseline justify-between">
                <h3 className="font-medium text-slate-100">{e.name}</h3>
                <span className="text-xs text-slate-400">{list.length} parts per copy</span>
              </div>
              {multi && list.length === widest && (
                <div className="mb-2 rounded bg-slate-800 px-2 py-1 text-xs text-slate-300">
                  All {list.length} parts must be present at the same time. One missing part stops the line.
                </div>
              )}
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {list.map((p) => {
                  const risk = partRisk(p);
                  const color = tone[risk];
                  const pct = p.needed > 0 ? Math.min(100, Math.round((p.usable / p.needed) * 100)) : 100;
                  const dry = hhmm(p.runs_dry);
                  const isBinding = binding?.part === p.part && p.needed > 0 && list.length > 1;
                  return (
                    <button
                      key={p.part}
                      onClick={() => onSelect(selected === p.part ? null : p.part)}
                      className="rounded-md border p-2 text-left"
                      style={{
                        borderColor: selected === p.part ? COLOR.accent : isBinding ? color : "#1e293b",
                        background: selected === p.part ? "rgba(90,169,255,0.12)" : "rgba(15,23,42,0.6)",
                      }}
                    >
                      <div className="flex items-center gap-2">
                        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke={color} strokeWidth="1.6">
                          <path d={icon(`${p.description ?? ""} ${p.part}`)} />
                        </svg>
                        <span className="truncate text-xs text-slate-200">{p.description || p.part}</span>
                      </div>
                      <div className="mt-2 h-1.5 rounded bg-slate-800">
                        <div className="h-1.5 rounded" style={{ width: `${pct}%`, background: color }} />
                      </div>
                      <div className="mt-1 flex justify-between text-[11px] text-slate-400">
                        <span>
                          {num(p.usable)} / {num(p.needed)}
                        </span>
                        {p.gap > 0 && <span style={{ color: COLOR.red }}>short {num(p.gap)}</span>}
                      </div>
                      {dry && <div className="text-[11px]" style={{ color: COLOR.amber }}>dry {dry}</div>}
                      {isBinding && <div className="text-[10px] uppercase tracking-wide text-slate-300">binding part</div>}
                    </button>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
