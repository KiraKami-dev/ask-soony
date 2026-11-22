// Who uses this device: the buyer (lands on Today) or the approver who signs money (lands on
// Launch). Remembered per device; no choice means Today. Names come from /api/state.

export type Role = 'buyer' | 'approver'

const KEY = 'ask-soony.as'

export function savedRole(): Role | null {
  try {
    const v = window.localStorage.getItem(KEY)
    return v === 'buyer' || v === 'approver' ? v : null
  } catch {
    return null
  }
}

export function saveRole(r: Role | null) {
  try {
    if (r) window.localStorage.setItem(KEY, r)
    else window.localStorage.removeItem(KEY)
  } catch { /* private mode: the choice lasts this visit */ }
}

/** First name as people say it: 'Dr. Maren Vogt, Head of Procurement' -> 'Maren'. */
export const firstNameOf = (s?: string | null) => (s ?? '').split(',')[0].split(' ').find((w) => w && !w.endsWith('.')) || ''

/** ?as=<first name | buyer | approver>: the role it names, or null when it names nobody. */
export function roleFromParam(v: string | null, names: { viewer?: string | null; approver?: string | null }): Role | null {
  const x = (v ?? '').trim().toLowerCase()
  if (!x) return null
  if (x === 'buyer' || x === 'approver') return x
  if (names.approver && firstNameOf(names.approver).toLowerCase() === x) return 'approver'
  if (names.viewer && firstNameOf(names.viewer).toLowerCase() === x) return 'buyer'
  return null
}
