/**
 * Supabase client — auth only. All data goes through our API, never straight to
 * Postgres, so scheduling logic lives in one place and the client cannot be trusted
 * with it.
 */
import { createClient } from '@supabase/supabase-js'

const url = import.meta.env.VITE_SUPABASE_URL
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

export const isAuthConfigured = Boolean(url && anonKey)

if (!isAuthConfigured && import.meta.env.PROD) {
  // Loud in production, tolerable in development so the app still boots before a
  // Supabase project exists.
  console.error('VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY are required')
}

export const supabase = createClient(url ?? 'http://localhost:54321', anonKey ?? 'anon', {
  auth: {
    persistSession: true,
    autoRefreshToken: true,
    detectSessionInUrl: true,
  },
})

export async function accessToken(): Promise<string | null> {
  const { data } = await supabase.auth.getSession()
  return data.session?.access_token ?? null
}
