/**
 * The offline outbox.
 *
 * A review is written here the instant the learner grades a card, before any network
 * call. That ordering is the whole point: if the request fails, the tab closes, or the
 * phone loses signal mid-session, the review is already durable and will sync later.
 *
 * Each review carries a UUIDv7 generated at review time. That id is what makes a retry
 * safe — the server absorbs an id it already holds rather than double-counting it — so
 * we can retry aggressively without tracking whether a request "really" failed.
 */
import Dexie, { type Table } from 'dexie'

import { uuidv7 } from '../lib/uuid'
import type { QueueCard, Rating } from '../lib/types'

export interface PendingReview {
  id: string
  cardId: string
  rating: Rating
  reviewedAt: string
  durationMs: number | null
  /** Bumped on each failed sync. Kept for diagnostics; we never drop a review. */
  attempts: number
}

export interface CachedCard {
  cardId: string
  deckId: string | null
  payload: QueueCard
  cachedAt: number
}

class LgappDb extends Dexie {
  outbox!: Table<PendingReview, string>
  queue!: Table<CachedCard, string>

  constructor() {
    super('lgapp')
    this.version(1).stores({
      outbox: 'id, cardId, reviewedAt',
      queue: 'cardId, deckId',
    })
  }
}

export const db = new LgappDb()

export async function enqueueReview(input: {
  cardId: string
  rating: Rating
  durationMs: number | null
  reviewedAt?: Date
}): Promise<PendingReview> {
  const reviewedAt = input.reviewedAt ?? new Date()
  const review: PendingReview = {
    // v7 so the id sorts by review time, and so a retry reuses the same id.
    id: uuidv7(reviewedAt.getTime()),
    cardId: input.cardId,
    rating: input.rating,
    reviewedAt: reviewedAt.toISOString(),
    durationMs: input.durationMs,
    attempts: 0,
  }
  await db.outbox.add(review)
  return review
}

export async function pendingReviews(limit = 500): Promise<PendingReview[]> {
  // Oldest first: the server sorts by reviewed_at anyway, but draining in order keeps
  // the common case on the cheap incremental path rather than triggering a replay.
  return db.outbox.orderBy('reviewedAt').limit(limit).toArray()
}

export async function pendingCount(): Promise<number> {
  return db.outbox.count()
}

export async function clearSynced(ids: string[]): Promise<void> {
  await db.outbox.bulkDelete(ids)
}

export async function recordAttempt(ids: string[]): Promise<void> {
  await db.transaction('rw', db.outbox, async () => {
    for (const id of ids) {
      const row = await db.outbox.get(id)
      if (row) await db.outbox.update(id, { attempts: row.attempts + 1 })
    }
  })
}

export async function cacheQueue(cards: QueueCard[], deckId: string | null): Promise<void> {
  const now = Date.now()
  await db.transaction('rw', db.queue, async () => {
    await db.queue.where('deckId').equals(deckId ?? '').delete()
    await db.queue.bulkPut(
      cards.map((card) => ({
        cardId: card.card.id,
        deckId: deckId ?? '',
        payload: card,
        cachedAt: now,
      })),
    )
  })
}

export async function cachedQueue(deckId: string | null): Promise<QueueCard[]> {
  const rows = await db.queue.where('deckId').equals(deckId ?? '').toArray()
  return rows.map((row) => row.payload)
}

export async function dropFromCachedQueue(cardIds: string[]): Promise<void> {
  await db.queue.bulkDelete(cardIds)
}
