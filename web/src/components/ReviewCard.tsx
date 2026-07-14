import { RATING_LABELS, Rating, type QueueCard } from '../lib/types'

const RATING_ORDER: Rating[] = [Rating.Again, Rating.Hard, Rating.Good, Rating.Easy]

const RATING_STYLES: Record<Rating, string> = {
  [Rating.Again]: 'bg-rose-600 hover:bg-rose-500 active:bg-rose-700',
  [Rating.Hard]: 'bg-amber-600 hover:bg-amber-500 active:bg-amber-700',
  [Rating.Good]: 'bg-sky-600 hover:bg-sky-500 active:bg-sky-700',
  [Rating.Easy]: 'bg-emerald-600 hover:bg-emerald-500 active:bg-emerald-700',
}

interface Props {
  card: QueueCard
  revealed: boolean
  onReveal: () => void
  onGrade: (rating: Rating) => void
}

export function ReviewCard({ card, revealed, onReveal, onGrade }: Props) {
  const { card: content } = card
  // The article is part of the word for a German learner — "Haus" without "das" is only
  // half the card.
  const prompt = content.article ? `${content.article} ${content.german}` : content.german

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-1 flex-col items-center justify-center gap-6 px-6 text-center">
        {card.is_new && (
          <span className="rounded-full bg-sky-500/15 px-3 py-1 text-xs font-medium text-sky-300">
            New card
          </span>
        )}

        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">{prompt}</h1>

        {content.plural && (
          <p className="text-sm text-slate-500">plural: die {content.plural}</p>
        )}

        {revealed ? (
          <div className="flex flex-col items-center gap-4">
            <p className="text-2xl text-sky-300">{content.english}</p>
            {content.example_de && (
              <div className="max-w-md space-y-1 rounded-lg bg-slate-900/70 p-4 text-sm">
                <p className="text-slate-200">{content.example_de}</p>
                <p className="text-slate-500">{content.example_en}</p>
              </div>
            )}
          </div>
        ) : (
          <p className="text-sm text-slate-500">
            Recall the meaning, then reveal.
          </p>
        )}
      </div>

      <div className="safe-bottom px-4 pt-4">
        {revealed ? (
          <div className="grid grid-cols-4 gap-2">
            {RATING_ORDER.map((rating) => (
              <button
                key={rating}
                onClick={() => onGrade(rating)}
                className={`rounded-xl py-4 text-sm font-semibold transition-colors ${RATING_STYLES[rating]}`}
              >
                {RATING_LABELS[rating]}
                {/* Desktop shortcuts; harmless noise on a phone. */}
                <span className="mt-0.5 block text-xs font-normal opacity-60">{rating}</span>
              </button>
            ))}
          </div>
        ) : (
          <button
            onClick={onReveal}
            className="w-full rounded-xl bg-slate-100 py-4 text-sm font-semibold text-slate-900 transition-colors hover:bg-white"
          >
            Show answer
            <span className="mt-0.5 block text-xs font-normal opacity-60">Space</span>
          </button>
        )}
      </div>
    </div>
  )
}
