/**
 * Sync tests.
 *
 * The rules being protected: never lose a review to a network failure, never let one bad
 * review block every later one, and never delete anything the server has not confirmed.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, OfflineError, api } from './api'
import { syncOutbox } from './sync'
import { db, enqueueReview, pendingCount } from '../db/outbox'
import { Rating } from './types'

vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api')
  return { ...actual, api: { ...actual.api, submitReviews: vi.fn() } }
})

const submitReviews = vi.mocked(api.submitReviews)

async function queueOne(cardId = 'card-1') {
  return enqueueReview({ cardId, rating: Rating.Good, durationMs: 1200 })
}

beforeEach(async () => {
  await db.outbox.clear()
  await db.queue.clear()
  submitReviews.mockReset()
})

describe('syncOutbox', () => {
  it('does nothing when there is nothing queued', async () => {
    const result = await syncOutbox()
    expect(result.synced).toBe(0)
    expect(submitReviews).not.toHaveBeenCalled()
  })

  it('sends queued reviews and clears them once the server confirms', async () => {
    await queueOne()
    submitReviews.mockResolvedValue({ cards: [] })

    const result = await syncOutbox()

    expect(result.synced).toBe(1)
    expect(await pendingCount()).toBe(0)
  })

  it('keeps reviews when the network is unreachable', async () => {
    // The core promise of the outbox: reviewing offline never loses work.
    await queueOne()
    submitReviews.mockRejectedValue(new OfflineError())

    const result = await syncOutbox()

    expect(result.synced).toBe(0)
    expect(result.remaining).toBe(1)
    expect(await pendingCount()).toBe(1)
  })

  it('keeps reviews when the server errors', async () => {
    await queueOne()
    submitReviews.mockRejectedValue(new ApiError(500, 'Internal Server Error'))

    await syncOutbox()

    expect(await pendingCount()).toBe(1)
  })

  it('keeps reviews when the token has expired', async () => {
    // A 401 is recoverable — the session refreshes and we try again.
    await queueOne()
    submitReviews.mockRejectedValue(new ApiError(401, 'Not authenticated'))

    await syncOutbox()

    expect(await pendingCount()).toBe(1)
  })

  it('drops reviews the server permanently rejects', async () => {
    // A 422 would fail identically forever. Keeping it would block every later review
    // behind it, so a few unacceptable reviews are sacrificed to save the rest.
    await queueOne()
    submitReviews.mockRejectedValue(new ApiError(422, 'Validation failed'))

    const result = await syncOutbox()

    expect(result.dropped).toBe(1)
    expect(await pendingCount()).toBe(0)
  })

  it('counts a failed attempt without losing the review', async () => {
    const review = await queueOne()
    submitReviews.mockRejectedValue(new OfflineError())

    await syncOutbox()

    expect((await db.outbox.get(review.id))?.attempts).toBe(1)
  })

  it('sends the whole batch in one request', async () => {
    // An offline session drains as one call, not one per card.
    for (let i = 0; i < 5; i++) await queueOne(`card-${i}`)
    submitReviews.mockResolvedValue({ cards: [] })

    await syncOutbox()

    expect(submitReviews).toHaveBeenCalledTimes(1)
    expect(submitReviews.mock.calls[0]?.[0]).toHaveLength(5)
  })

  it('sends reviews oldest first', async () => {
    // Draining in order keeps the server on its cheap incremental path.
    await enqueueReview({
      cardId: 'later',
      rating: Rating.Good,
      durationMs: 10,
      reviewedAt: new Date('2026-07-14T10:00:00Z'),
    })
    await enqueueReview({
      cardId: 'earlier',
      rating: Rating.Good,
      durationMs: 10,
      reviewedAt: new Date('2026-07-14T09:00:00Z'),
    })
    submitReviews.mockResolvedValue({ cards: [] })

    await syncOutbox()

    expect(submitReviews.mock.calls[0]?.[0].map((r) => r.card_id)).toEqual(['earlier', 'later'])
  })

  it('coalesces concurrent drains into one request', async () => {
    // A reconnect and a manual flush can fire together; sending twice wastes a trip.
    await queueOne()
    submitReviews.mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve({ cards: [] }), 10)),
    )

    const [a, b] = await Promise.all([syncOutbox(), syncOutbox()])

    expect(submitReviews).toHaveBeenCalledTimes(1)
    expect(a).toEqual(b)
  })

  it('sends the id the review was created with', async () => {
    // The id is the server's idempotency key; regenerating it would defeat the whole
    // retry-safety design.
    const review = await queueOne()
    submitReviews.mockResolvedValue({ cards: [] })

    await syncOutbox()

    expect(submitReviews.mock.calls[0]?.[0][0]?.id).toBe(review.id)
  })

  it('reuses the same id across retries', async () => {
    const review = await queueOne()
    submitReviews.mockRejectedValueOnce(new OfflineError())
    await syncOutbox()

    submitReviews.mockResolvedValue({ cards: [] })
    await syncOutbox()

    expect(submitReviews.mock.calls[1]?.[0][0]?.id).toBe(review.id)
  })
})
