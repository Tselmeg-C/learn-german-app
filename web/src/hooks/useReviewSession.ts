/**
 * The review session.
 *
 * The important ordering: grading a card writes to the durable outbox *first*, then
 * advances the UI, and only then tries to sync. The learner is never made to wait for
 * the network to see the next card, and a review survives a crash between grading and
 * syncing.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { cacheQueue, cachedQueue, dropFromCachedQueue, enqueueReview } from '../db/outbox'
import { OfflineError, api } from '../lib/api'
import { syncOutbox } from '../lib/sync'
import type { QueueCard, Rating } from '../lib/types'

export type SessionStatus = 'loading' | 'reviewing' | 'done' | 'empty' | 'error'

export interface ReviewSession {
  status: SessionStatus
  current: QueueCard | null
  revealed: boolean
  reviewed: number
  remaining: number
  reveal: () => void
  grade: (rating: Rating) => Promise<void>
  fromCache: boolean
}

export function useReviewSession(deckId?: string): ReviewSession {
  const [cards, setCards] = useState<QueueCard[]>([])
  const [index, setIndex] = useState(0)
  const [revealed, setRevealed] = useState(false)
  const [status, setStatus] = useState<SessionStatus>('loading')
  const [fromCache, setFromCache] = useState(false)
  const shownAt = useRef<number>(Date.now())

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const queue = await api.queue(deckId)
        if (cancelled) return
        await cacheQueue(queue.cards, deckId ?? null)
        setCards(queue.cards)
        setFromCache(false)
        setStatus(queue.cards.length ? 'reviewing' : 'empty')
      } catch (error) {
        // The cached queue is what makes a session possible on the underground, so fall
        // back to it whatever went wrong.
        const cached = await cachedQueue(deckId ?? null)
        if (cancelled) return

        if (cached.length) {
          setCards(cached)
          setFromCache(true)
          setStatus('reviewing')
          return
        }

        // Nothing cached. Being offline with an empty cache genuinely means there is
        // nothing to review; a rejected or broken request does not, and must not be
        // dressed up as "you're all caught up" — that hides an expired session or an
        // outage behind a congratulation.
        setStatus(error instanceof OfflineError ? 'empty' : 'error')
      }
    }

    void load()
    return () => {
      cancelled = true
    }
  }, [deckId])

  useEffect(() => {
    shownAt.current = Date.now()
  }, [index])

  const current = cards[index] ?? null

  const grade = useCallback(
    async (rating: Rating) => {
      if (!current) return

      // Durable first. If anything below throws, the review is already safe.
      await enqueueReview({
        cardId: current.card.id,
        rating,
        durationMs: Date.now() - shownAt.current,
      })
      await dropFromCachedQueue([current.card.id])

      // Advance immediately — the learner should never wait on the network.
      setRevealed(false)
      setIndex((i) => {
        const next = i + 1
        if (next >= cards.length) setStatus('done')
        return next
      })

      // Fire and forget: failure just leaves the review in the outbox for later.
      void syncOutbox()
    },
    [current, cards.length],
  )

  const reveal = useCallback(() => setRevealed(true), [])

  return useMemo(
    () => ({
      status,
      current,
      revealed,
      reviewed: index,
      remaining: Math.max(0, cards.length - index),
      reveal,
      grade,
      fromCache,
    }),
    [status, current, revealed, index, cards.length, reveal, grade, fromCache],
  )
}
