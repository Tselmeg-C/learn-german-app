import { useOnlineStatus } from '../hooks/useOnlineStatus'
import { usePendingCount } from '../hooks/usePendingCount'

/**
 * Tells the learner their offline reviews are safe.
 *
 * Without this, reviewing offline feels like shouting into a void — the app looks
 * identical whether the work is queued or lost.
 */
export function SyncBadge() {
  const online = useOnlineStatus()
  const pending = usePendingCount()

  if (online && pending === 0) return null

  return (
    <div
      className={`rounded-full px-3 py-1 text-xs font-medium ${
        online ? 'bg-sky-500/15 text-sky-300' : 'bg-amber-500/15 text-amber-300'
      }`}
      role="status"
    >
      {!online && pending > 0 && `Offline · ${pending} saved`}
      {!online && pending === 0 && 'Offline'}
      {online && pending > 0 && `Syncing ${pending}…`}
    </div>
  )
}
