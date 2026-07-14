import { useLiveQuery } from 'dexie-react-hooks'

import { db } from '../db/outbox'

/** Live count of unsynced reviews, straight from IndexedDB. */
export function usePendingCount(): number {
  return useLiveQuery(() => db.outbox.count(), [], 0) ?? 0
}
