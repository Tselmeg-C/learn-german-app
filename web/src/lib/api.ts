/**
 * Typed API client.
 *
 * Every response type comes from the generated OpenAPI schema, so this file describes
 * how to call the API but never what it returns.
 */
import { accessToken } from './supabase'
import type { Deck, Queue, ReviewBatchOut, Stats, SubscribeOut } from './types'

const BASE = import.meta.env.VITE_API_URL ?? ''

export class ApiError extends Error {
  // Declared as fields rather than constructor parameter properties: the latter emit
  // runtime code, which `erasableSyntaxOnly` forbids.
  readonly status: number
  readonly title: string
  readonly detail?: string

  constructor(status: number, title: string, detail?: string) {
    super(detail ? `${title}: ${detail}` : title)
    this.name = 'ApiError'
    this.status = status
    this.title = title
    this.detail = detail
  }
}

/** True when the request failed because the network is unreachable, not because the
 * server said no. The caller treats these very differently: one is retryable, the other
 * is a bug or a real rejection. */
export class OfflineError extends Error {
  constructor() {
    super('offline')
    this.name = 'OfflineError'
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await accessToken()
  const headers = new Headers(init.headers)
  headers.set('accept', 'application/json')
  if (init.body) headers.set('content-type', 'application/json')
  if (token) headers.set('authorization', `Bearer ${token}`)

  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, { ...init, headers })
  } catch {
    // fetch only rejects on a network-level failure; an HTTP error is a resolved promise.
    throw new OfflineError()
  }

  if (!response.ok) {
    // Errors are RFC 9457 problem+json, but a proxy or gateway may return HTML.
    const problem = await response.json().catch(() => null)
    throw new ApiError(
      response.status,
      problem?.title ?? response.statusText,
      problem?.detail,
    )
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  decks: () => request<Deck[]>('/v1/decks'),

  subscribe: (deckId: string) =>
    request<SubscribeOut>(`/v1/decks/${deckId}/subscribe`, { method: 'POST' }),

  queue: (deckId?: string, limit = 50) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (deckId) params.set('deck_id', deckId)
    return request<Queue>(`/v1/reviews/queue?${params}`)
  },

  submitReviews: (
    reviews: Array<{
      id: string
      card_id: string
      rating: number
      reviewed_at: string
      duration_ms: number | null
    }>,
  ) =>
    request<ReviewBatchOut>('/v1/reviews', {
      method: 'POST',
      body: JSON.stringify({ reviews }),
    }),

  stats: () => request<Stats>('/v1/stats'),
}
