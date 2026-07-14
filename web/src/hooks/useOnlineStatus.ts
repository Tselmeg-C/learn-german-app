import { useEffect, useState } from 'react'

/** navigator.onLine only knows whether there is *a* network, not whether our API is
 * reachable — but it is a good enough signal for telling the learner why their reviews
 * are still queued. */
export function useOnlineStatus(): boolean {
  const [online, setOnline] = useState(() => navigator.onLine)

  useEffect(() => {
    const on = () => setOnline(true)
    const off = () => setOnline(false)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => {
      window.removeEventListener('online', on)
      window.removeEventListener('offline', off)
    }
  }, [])

  return online
}
