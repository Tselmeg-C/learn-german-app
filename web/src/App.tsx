import {
  Link,
  Outlet,
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  useNavigate,
  useParams,
} from '@tanstack/react-router'
import { useEffect } from 'react'

import { SyncBadge } from './components/SyncBadge'
import { useAuth } from './hooks/useAuth'
import { installSyncTriggers } from './lib/sync'
import { supabase } from './lib/supabase'
import { Decks } from './pages/Decks'
import { Login } from './pages/Login'
import { Review } from './pages/Review'
import { Stats } from './pages/Stats'

function Shell() {
  return (
    <div className="mx-auto flex min-h-dvh max-w-2xl flex-col">
      <header className="flex items-center justify-between gap-4 border-b border-slate-900 px-4 py-3">
        <nav className="flex items-center gap-4 text-sm">
          <Link
            to="/"
            className="font-medium text-slate-400 transition-colors hover:text-slate-100"
            activeProps={{ className: 'font-medium text-slate-100' }}
            activeOptions={{ exact: true }}
          >
            Decks
          </Link>
          <Link
            to="/stats"
            className="font-medium text-slate-400 transition-colors hover:text-slate-100"
            activeProps={{ className: 'font-medium text-slate-100' }}
          >
            Progress
          </Link>
        </nav>
        <div className="flex items-center gap-3">
          <SyncBadge />
          <button
            onClick={() => supabase.auth.signOut()}
            className="text-xs text-slate-500 transition-colors hover:text-slate-300"
          >
            Sign out
          </button>
        </div>
      </header>

      <main className="flex flex-1 flex-col">
        <Outlet />
      </main>
    </div>
  )
}

const rootRoute = createRootRoute({ component: Shell })

const decksRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: function DecksPage() {
    const navigate = useNavigate()
    return <Decks onReview={(deckId) => navigate({ to: '/review/$deckId', params: { deckId } })} />
  },
})

const reviewRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/review/$deckId',
  component: function ReviewPage() {
    const { deckId } = useParams({ from: '/review/$deckId' })
    const navigate = useNavigate()
    return <Review deckId={deckId} onDone={() => navigate({ to: '/' })} />
  },
})

const statsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/stats',
  component: Stats,
})

const router = createRouter({
  routeTree: rootRoute.addChildren([decksRoute, reviewRoute, statsRoute]),
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

export function App() {
  const { session, loading } = useAuth()

  // Drain the outbox on reconnect and on focus, for the whole life of the app rather
  // than only while the review screen is mounted — reviews may be waiting from a session
  // that ended when the phone went into a tunnel.
  useEffect(() => {
    if (!session) return
    return installSyncTriggers()
  }, [session])

  if (loading) {
    return <div className="grid min-h-dvh place-items-center text-sm text-slate-500">…</div>
  }

  if (!session) return <Login />

  return <RouterProvider router={router} />
}
