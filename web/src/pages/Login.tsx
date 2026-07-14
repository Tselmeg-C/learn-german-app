import { useState } from 'react'

import { supabase } from '../lib/supabase'

export function Login() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function signInWithEmail(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: window.location.origin },
    })
    setBusy(false)
    if (error) setError(error.message)
    else setSent(true)
  }

  async function signInWithGoogle() {
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: window.location.origin },
    })
    if (error) setError(error.message)
  }

  return (
    <div className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center gap-8 px-6">
      <div className="space-y-2 text-center">
        <h1 className="text-3xl font-semibold tracking-tight">Learn German</h1>
        <p className="text-sm text-slate-400">Spaced repetition that fits your memory.</p>
      </div>

      {sent ? (
        <p className="rounded-lg bg-emerald-500/10 p-4 text-center text-sm text-emerald-300">
          Check <span className="font-medium">{email}</span> for a sign-in link.
        </p>
      ) : (
        <form onSubmit={signInWithEmail} className="space-y-3">
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            autoComplete="email"
            className="w-full rounded-lg border border-slate-800 bg-slate-900 px-4 py-3 text-sm outline-none focus:border-sky-500"
          />
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-sky-600 py-3 text-sm font-semibold transition-colors hover:bg-sky-500 disabled:opacity-50"
          >
            {busy ? 'Sending…' : 'Email me a link'}
          </button>
        </form>
      )}

      <div className="flex items-center gap-3 text-xs text-slate-600">
        <div className="h-px flex-1 bg-slate-800" />
        or
        <div className="h-px flex-1 bg-slate-800" />
      </div>

      <button
        onClick={signInWithGoogle}
        className="w-full rounded-lg border border-slate-800 py-3 text-sm font-medium transition-colors hover:bg-slate-900"
      >
        Continue with Google
      </button>

      {error && <p className="text-center text-sm text-rose-400">{error}</p>}
    </div>
  )
}
