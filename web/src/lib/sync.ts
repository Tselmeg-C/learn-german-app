/**
 * Draining the outbox.
 *
 * Rules, and why:
 *
 * - A review is only deleted locally once the server has confirmed it. Anything else
 *   risks losing a review to a dropped response.
 * - A network failure leaves the outbox untouched and we try again later. A 4xx that is
 *   *about the payload* (422, 404) would fail identically forever, so those reviews are
 *   dropped rather than poisoning the queue and blocking every later sync behind them.
 * - Only one drain runs at a time. Two concurrent drains would send the same reviews
 *   twice; the server would absorb them, but it wastes a round trip and confuses the
 *   pending count.
 */
import { ApiError, OfflineError, api } from './api'
import { clearSynced, pendingReviews, recordAttempt } from '../db/outbox'
import type { CardState } from './types'

export interface SyncResult {
  synced: number
  dropped: number
  remaining: number
  cards: CardState[]
}

const EMPTY: SyncResult = { synced: 0, dropped: 0, remaining: 0, cards: [] }

let inFlight: Promise<SyncResult> | null = null

export async function syncOutbox(): Promise<SyncResult> {
  // Coalesce: a reconnect event and a manual flush can fire together.
  if (inFlight) return inFlight
  inFlight = drain().finally(() => {
    inFlight = null
  })
  return inFlight
}

async function drain(): Promise<SyncResult> {
  const pending = await pendingReviews()
  if (pending.length === 0) return EMPTY

  const ids = pending.map((r) => r.id)

  try {
    const result = await api.submitReviews(
      pending.map((r) => ({
        id: r.id,
        card_id: r.cardId,
        rating: r.rating,
        reviewed_at: r.reviewedAt,
        duration_ms: r.durationMs,
      })),
    )
    await clearSynced(ids)
    return { synced: pending.length, dropped: 0, remaining: 0, cards: result.cards }
  } catch (error) {
    if (error instanceof OfflineError) {
      await recordAttempt(ids)
      return { ...EMPTY, remaining: pending.length }
    }

    if (error instanceof ApiError && isPermanent(error)) {
      // Retrying can only fail the same way, and leaving these queued would block every
      // later review behind them. Losing a few unacceptable reviews beats losing all
      // subsequent ones.
      console.error('dropping reviews the server permanently rejected', error)
      await clearSynced(ids)
      return { synced: 0, dropped: pending.length, remaining: 0, cards: [] }
    }

    // 5xx, 401 and anything unrecognised: keep them and try again later.
    await recordAttempt(ids)
    return { ...EMPTY, remaining: pending.length }
  }
}

function isPermanent(error: ApiError): boolean {
  return error.status === 422 || error.status === 404
}

/** Sync on reconnect, on tab focus, and when the page is hidden (the mobile case: the
 * app is usually backgrounded, not closed). */
export function installSyncTriggers(onSync?: (result: SyncResult) => void): () => void {
  const run = () => {
    void syncOutbox().then((result) => {
      if (result.synced || result.dropped) onSync?.(result)
    })
  }

  const onVisibility = () => {
    if (document.visibilityState === 'visible') run()
  }

  window.addEventListener('online', run)
  document.addEventListener('visibilitychange', onVisibility)
  // Best-effort final flush; not guaranteed, which is exactly why the outbox is durable.
  window.addEventListener('pagehide', run)

  run()

  return () => {
    window.removeEventListener('online', run)
    document.removeEventListener('visibilitychange', onVisibility)
    window.removeEventListener('pagehide', run)
  }
}
