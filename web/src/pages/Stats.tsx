import { useQuery } from '@tanstack/react-query'

import { api } from '../lib/api'
import type { Stats as StatsData } from '../lib/types'

export function Stats() {
  const { data, isLoading, error } = useQuery({ queryKey: ['stats'], queryFn: api.stats })

  if (isLoading) return <p className="p-6 text-sm text-slate-500">Loading…</p>
  if (error || !data) return <p className="p-6 text-sm text-rose-400">Could not load stats.</p>

  return (
    <div className="space-y-6 p-4">
      <div className="grid grid-cols-2 gap-3">
        <Tile label="Reviews today" value={data.reviews_today} />
        <Tile label="Streak" value={`${data.streak_days}d`} />
        <Tile label="Due now" value={data.due_today} />
        <Tile
          label="Retention"
          // Null/absent means "not enough mature cards to say", which is not the same as
          // 0%. Checked loosely because the field is optional in the schema, and
          // `undefined * 100` would render a confident "NaN%".
          value={data.retention_rate == null ? '—' : `${Math.round(data.retention_rate * 100)}%`}
          hint={data.retention_rate == null ? 'Needs mature cards' : 'On mature cards'}
        />
      </div>

      <Section title="Cards">
        <div className="flex gap-2">
          {(['new', 'learning', 'relearning', 'review'] as const).map((state) => (
            <div key={state} className="flex-1 rounded-lg bg-slate-900/70 p-3 text-center">
              <p className="text-lg font-semibold">{data.cards_by_state[state] ?? 0}</p>
              <p className="mt-0.5 text-xs capitalize text-slate-500">{state}</p>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Last 30 days">
        <ReviewChart data={data} />
        <p className="mt-2 text-xs text-slate-500">{data.reviews_total} reviews all time</p>
      </Section>
    </div>
  )
}

function ReviewChart({ data }: { data: StatsData }) {
  if (!data.reviews_per_day.length) {
    return <p className="text-sm text-slate-500">No reviews yet.</p>
  }

  const max = Math.max(...data.reviews_per_day.map((d) => d.count), 1)

  return (
    <div className="flex h-24 items-end gap-1" role="img" aria-label="Reviews per day">
      {data.reviews_per_day.map((day) => (
        <div
          key={day.day}
          className="min-w-1 flex-1 rounded-t bg-sky-500/70"
          // A day with reviews always shows something, so "few" never renders as "none".
          style={{ height: `${Math.max(6, (day.count / max) * 100)}%` }}
          title={`${day.day}: ${day.count}`}
        />
      ))}
    </div>
  )
}

function Tile({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
      <p className="text-2xl font-semibold">{value}</p>
      <p className="mt-1 text-xs text-slate-500">{label}</p>
      {hint && <p className="mt-0.5 text-[10px] text-slate-600">{hint}</p>}
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">{title}</h2>
      {children}
    </section>
  )
}
