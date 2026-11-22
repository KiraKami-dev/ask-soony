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

/**
 * What reaches the person who signs: cases that are red for the launch, that she signs, or that
 * cost money. Everything else stays with the buyer, who sees every case.
 */
export function mattersToApprover(
  c: { id: string; level?: string; signoff?: string | null; approver?: string | null },
  approver: string | null | undefined,
  priced: Set<string>,
): boolean {
  if (c.level === 'red' || priced.has(c.id)) return true
  const me = firstNameOf(approver)
  return !!me && (firstNameOf(c.signoff) === me || firstNameOf(c.approver) === me)
}

/** 'Dr. Maren Vogt, Head of Procurement' -> 'Head of Procurement'; no title given: the role's name. */
export function roleTitle(role: Role, label?: string | null): string {
  const t = (label ?? '').split(',').slice(1).join(',').trim()
  return t || (role === 'buyer' ? 'Buyer' : 'Approver')
}
