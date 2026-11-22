import { useState } from 'react'
import type { Card } from '@/lib/api'
import { pendingOf, useLive } from './live'
import { ProposalLines, ProposalSheet, WouldLine, groupProposals } from './Proposals'
import type { Proposal } from './types'

// What the buyer's routines say about the card in the decision pane, in the same quiet lines as
// Today: a step one of them prepared (review it in a sheet), or a routine that would handle it.

export function PaneHints({ card, onChanged }: { card: Card; onChanged: () => void }) {
  const live = useLive()
  const [review, setReview] = useState<Proposal[] | null>(null)
  const proposals = pendingOf(live, card.id)
  const asking = new Set(proposals.map((p) => p.routine_id))
  const would = card.status === 'open' ? (live.would[card.id] ?? []).filter((w) => !asking.has(w.routine_id)) : []
  const ctx = { cards: [card] }
  if (!proposals.length && !would.length && !review) return null
  return (
    <div className="space-y-2">
      <ProposalLines groups={groupProposals(proposals, ctx)} onReview={setReview} />
      {would.map((w) => <WouldLine key={w.routine_id} w={w} onChanged={onChanged} />)}
      <ProposalSheet items={review} ctx={ctx} onClose={() => setReview(null)} onChanged={onChanged} />
    </div>
  )
}
