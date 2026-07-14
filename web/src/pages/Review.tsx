import { useEffect } from 'react'

import { ReviewCard } from '../components/ReviewCard'
import { useReviewSession } from '../hooks/useReviewSession'
import { Rating } from '../lib/types'

interface Props {
  deckId?: string
  onDone: () => void
}

export function Review({ deckId, onDone }: Props) {
  const session = useReviewSession(deckId)
  const { status, current, revealed, reveal, grade } = session

  // Keyboard shortcuts: space to reveal, 1-4 to grade. Desktop reviewers live here.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (status !== 'reviewing' || !current) return
      if (!revealed && (event.key === ' ' || event.key === 'Enter')) {
        event.preventDefault()
        reveal()
        return
      }
      if (revealed && ['1', '2', '3', '4'].includes(event.key)) {
        event.preventDefault()
        void grade(Number(event.key) as Rating)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [status, current, revealed, reveal, grade])

  if (status === 'loading') {
    return <p className="p-6 text-sm text-slate-500">Loading…</p>
  }

  if (status === 'error') {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
        <p className="text-lg">Could not load your cards.</p>
        <p className="text-sm text-slate-500">
          Something went wrong on our side. Any reviews you have already done are saved.
        </p>
        <button
          onClick={() => window.location.reload()}
          className="mt-2 rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium hover:bg-sky-500"
        >
          Try again
        </button>
        <button onClick={onDone} className="text-sm text-sky-400 hover:underline">
          Back to decks
        </button>
      </div>
    )
  }

  if (status === 'empty') {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
        <p className="text-lg">Nothing due right now.</p>
        <p className="text-sm text-slate-500">Come back later, or start another deck.</p>
        <button onClick={onDone} className="mt-2 text-sm text-sky-400 hover:underline">
          Back to decks
        </button>
      </div>
    )
  }

  if (status === 'done') {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
        <p className="text-lg">Session complete.</p>
        <p className="text-sm text-slate-500">
          {session.reviewed} card{session.reviewed === 1 ? '' : 's'} reviewed.
        </p>
        <button onClick={onDone} className="mt-2 text-sm text-sky-400 hover:underline">
          Back to decks
        </button>
      </div>
    )
  }

  return (
    <>
      <div className="px-4 pt-2">
        <div className="h-1 overflow-hidden rounded-full bg-slate-800">
          <div
            className="h-full bg-sky-500 transition-all duration-300"
            style={{
              width: `${(session.reviewed / (session.reviewed + session.remaining)) * 100}%`,
            }}
          />
        </div>
        {session.fromCache && (
          <p className="pt-2 text-center text-xs text-amber-400/80">
            Reviewing from your saved queue.
          </p>
        )}
      </div>

      {current && (
        <ReviewCard card={current} revealed={revealed} onReveal={reveal} onGrade={grade} />
      )}
    </>
  )
}
