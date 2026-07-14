/**
 * Review loop tests.
 *
 * The behaviour that matters: a graded card is durable before the network is involved,
 * and the learner is never blocked on a request. These drive the real component.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { Review } from './Review'
import { ApiError, OfflineError, api } from '../lib/api'
import { db, pendingCount, pendingReviews } from '../db/outbox'
import type { QueueCard } from '../lib/types'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api')
  return {
    ...actual,
    api: { ...actual.api, queue: vi.fn(), submitReviews: vi.fn() },
  }
})

const queue = vi.mocked(api.queue)
const submitReviews = vi.mocked(api.submitReviews)

function card(id: string, german: string, english: string, isNew = true): QueueCard {
  return {
    card: {
      id,
      german,
      english,
      article: 'das',
      part_of_speech: 'noun',
      plural: null,
      example_de: 'Das Haus ist groß.',
      example_en: 'The house is big.',
      tags: [],
    },
    state: 'learning',
    due: new Date().toISOString(),
    reps: isNew ? 0 : 3,
    is_new: isNew,
  }
}

beforeEach(async () => {
  await db.outbox.clear()
  await db.queue.clear()
  vi.clearAllMocks()
  submitReviews.mockResolvedValue({ cards: [] })
})

describe('Review', () => {
  it('shows the German prompt with its article, and hides the answer', async () => {
    queue.mockResolvedValue({ cards: [card('1', 'Haus', 'house')], due_total: 0, new_remaining_today: 20 })

    render(<Review onDone={vi.fn()} />)

    expect(await screen.findByText('das Haus')).toBeInTheDocument()
    expect(screen.queryByText('house')).not.toBeInTheDocument()
  })

  it('reveals the answer and its example', async () => {
    const user = userEvent.setup()
    queue.mockResolvedValue({ cards: [card('1', 'Haus', 'house')], due_total: 0, new_remaining_today: 20 })

    render(<Review onDone={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: /show answer/i }))

    expect(screen.getByText('house')).toBeInTheDocument()
    expect(screen.getByText('Das Haus ist groß.')).toBeInTheDocument()
  })

  it('writes the review to the outbox before the network is involved', async () => {
    // The ordering the design depends on. The request is left hanging, so anything
    // observed here was recorded without the server's help.
    const user = userEvent.setup()
    submitReviews.mockImplementation(() => new Promise(() => {}))
    queue.mockResolvedValue({
      cards: [card('card-1', 'Haus', 'house')],
      due_total: 0,
      new_remaining_today: 20,
    })

    render(<Review onDone={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: /show answer/i }))
    await user.click(screen.getByRole('button', { name: /good/i }))

    await waitFor(async () => expect(await pendingCount()).toBe(1))
    const [queued] = await pendingReviews()
    expect(queued?.cardId).toBe('card-1')
    expect(queued?.rating).toBe(3)
    expect(queued?.durationMs).toBeGreaterThanOrEqual(0)
    expect(queued?.reviewedAt).toBeTruthy()
  })

  it('records the review even when the network is down', async () => {
    // The whole point of the outbox: an offline grade must not be lost.
    const user = userEvent.setup()
    queue.mockResolvedValue({ cards: [card('card-1', 'Haus', 'house')], due_total: 0, new_remaining_today: 20 })
    submitReviews.mockRejectedValue(new Error('network down'))

    render(<Review onDone={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: /show answer/i }))
    await user.click(screen.getByRole('button', { name: /again/i }))

    await waitFor(async () => {
      const queued = await pendingReviews()
      expect(queued).toHaveLength(1)
      expect(queued[0]?.cardId).toBe('card-1')
      expect(queued[0]?.rating).toBe(1)
    })
  })

  it('advances to the next card without waiting for the network', async () => {
    const user = userEvent.setup()
    // A request that never resolves: the UI must move on regardless.
    submitReviews.mockImplementation(() => new Promise(() => {}))
    queue.mockResolvedValue({
      cards: [card('1', 'Haus', 'house'), card('2', 'Frau', 'woman')],
      due_total: 0,
      new_remaining_today: 20,
    })

    render(<Review onDone={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: /show answer/i }))
    await user.click(screen.getByRole('button', { name: /good/i }))

    expect(await screen.findByText('das Frau')).toBeInTheDocument()
  })

  it('falls back to the cached queue when offline', async () => {
    // Starting a session on the underground is the reason the queue is cached at all.
    queue.mockResolvedValueOnce({
      cards: [card('1', 'Haus', 'house')],
      due_total: 0,
      new_remaining_today: 20,
    })
    const { unmount } = render(<Review onDone={vi.fn()} />)
    await screen.findByText('das Haus')
    unmount()

    // Second mount with the network gone: the session must still start.
    queue.mockRejectedValue(new Error('offline'))
    render(<Review onDone={vi.fn()} />)

    expect(await screen.findByText('das Haus')).toBeInTheDocument()
    expect(screen.getByText(/saved queue/i)).toBeInTheDocument()
  })

  it('says so when nothing is due', async () => {
    queue.mockResolvedValue({ cards: [], due_total: 0, new_remaining_today: 0 })

    render(<Review onDone={vi.fn()} />)

    expect(await screen.findByText(/nothing due/i)).toBeInTheDocument()
  })

  it('reports an error rather than claiming nothing is due when the API rejects', async () => {
    // Found by driving the real app: a 401 rendered as "Nothing due right now", so an
    // expired session looked exactly like being caught up on reviews.
    queue.mockRejectedValue(new ApiError(401, 'Not authenticated'))

    render(<Review onDone={vi.fn()} />)

    expect(await screen.findByText(/could not load/i)).toBeInTheDocument()
    expect(screen.queryByText(/nothing due/i)).not.toBeInTheDocument()
  })

  it('reports an error when the server is broken', async () => {
    queue.mockRejectedValue(new ApiError(500, 'Internal Server Error'))

    render(<Review onDone={vi.fn()} />)

    expect(await screen.findByText(/could not load/i)).toBeInTheDocument()
  })

  it('says nothing is due when offline with an empty cache', async () => {
    // Offline with nothing saved really does mean there is nothing to review.
    queue.mockRejectedValue(new OfflineError())

    render(<Review onDone={vi.fn()} />)

    expect(await screen.findByText(/nothing due/i)).toBeInTheDocument()
  })

  it('finishes the session after the last card', async () => {
    const user = userEvent.setup()
    queue.mockResolvedValue({ cards: [card('1', 'Haus', 'house')], due_total: 0, new_remaining_today: 20 })

    render(<Review onDone={vi.fn()} />)
    await user.click(await screen.findByRole('button', { name: /show answer/i }))
    await user.click(screen.getByRole('button', { name: /easy/i }))

    expect(await screen.findByText(/session complete/i)).toBeInTheDocument()
  })

  it('supports keyboard shortcuts: space reveals, 1-4 grade', async () => {
    const user = userEvent.setup()
    submitReviews.mockImplementation(() => new Promise(() => {}))
    queue.mockResolvedValue({
      cards: [card('kb-1', 'Haus', 'house')],
      due_total: 0,
      new_remaining_today: 20,
    })

    render(<Review onDone={vi.fn()} />)
    await screen.findByText('das Haus')

    await user.keyboard(' ')
    expect(screen.getByText('house')).toBeInTheDocument()

    await user.keyboard('2') // Hard
    await waitFor(async () => expect(await pendingCount()).toBe(1))
    const [queued] = await pendingReviews()
    expect(queued?.rating).toBe(2)
    expect(await screen.findByText(/session complete/i)).toBeInTheDocument()
  })

  it('ignores grade keys before the answer is revealed', async () => {
    // Otherwise a stray keypress silently grades a card the learner never saw.
    const user = userEvent.setup()
    queue.mockResolvedValue({
      cards: [card('kb-2', 'Haus', 'house')],
      due_total: 0,
      new_remaining_today: 20,
    })

    render(<Review onDone={vi.fn()} />)
    await screen.findByText('das Haus')

    await user.keyboard('3')

    expect(await pendingCount()).toBe(0)
    expect(screen.getByText('das Haus')).toBeInTheDocument()
  })
})
