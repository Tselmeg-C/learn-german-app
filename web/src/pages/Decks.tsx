import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../lib/api'
import type { Deck } from '../lib/types'

interface Props {
  onReview: (deckId: string) => void
}

export function Decks({ onReview }: Props) {
  const queryClient = useQueryClient()
  const { data: decks, isLoading, error } = useQuery({ queryKey: ['decks'], queryFn: api.decks })

  const subscribe = useMutation({
    mutationFn: api.subscribe,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['decks'] }),
  })

  if (isLoading) return <p className="p-6 text-sm text-slate-500">Loading decks…</p>
  if (error) return <p className="p-6 text-sm text-rose-400">Could not load decks.</p>
  if (!decks?.length) {
    return (
      <div className="p-6 text-sm text-slate-500">
        <p>No decks yet.</p>
        <p className="mt-1 text-xs">Import content with `lgapp import-content`.</p>
      </div>
    )
  }

  return (
    <div className="space-y-3 p-4">
      {decks.map((deck) => (
        <DeckRow
          key={deck.id}
          deck={deck}
          onReview={() => onReview(deck.id)}
          onSubscribe={() => subscribe.mutate(deck.id)}
          subscribing={subscribe.isPending && subscribe.variables === deck.id}
        />
      ))}
    </div>
  )
}

function DeckRow({
  deck,
  onReview,
  onSubscribe,
  subscribing,
}: {
  deck: Deck
  onReview: () => void
  onSubscribe: () => void
  subscribing: boolean
}) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="font-medium">{deck.name}</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            {deck.card_count} cards
            {deck.cefr_level && ` · ${deck.cefr_level}`}
            {deck.due_count > 0 && (
              <span className="text-sky-400"> · {deck.due_count} due</span>
            )}
          </p>
        </div>

        {deck.subscribed ? (
          <button
            onClick={onReview}
            className="shrink-0 rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium transition-colors hover:bg-sky-500"
          >
            Review
          </button>
        ) : (
          <button
            onClick={onSubscribe}
            disabled={subscribing}
            className="shrink-0 rounded-lg border border-slate-700 px-4 py-2 text-sm font-medium transition-colors hover:bg-slate-800 disabled:opacity-50"
          >
            {subscribing ? 'Adding…' : 'Start'}
          </button>
        )}
      </div>
    </div>
  )
}
