/**
 * Application types, aliased from the generated OpenAPI schema.
 *
 * Nothing here is hand-written structure: `api-types.ts` is generated from the API's own
 * OpenAPI document (`npm run gen:api`), so a server-side schema change breaks the
 * typecheck here rather than surfacing as a runtime bug.
 */
import type { components } from './api-types'

export type Deck = components['schemas']['DeckOut']
export type Card = components['schemas']['CardOut']
export type QueueCard = components['schemas']['QueueCardOut']
export type Queue = components['schemas']['QueueOut']
export type CardState = components['schemas']['CardStateOut']
export type ReviewBatchOut = components['schemas']['ReviewBatchOut']
export type Stats = components['schemas']['StatsOut']
export type SubscribeOut = components['schemas']['SubscribeOut']

/** FSRS grades. The numbers are the wire format; the names are for humans. */
export const Rating = {
  Again: 1,
  Hard: 2,
  Good: 3,
  Easy: 4,
} as const

export type Rating = (typeof Rating)[keyof typeof Rating]

export const RATING_LABELS: Record<Rating, string> = {
  [Rating.Again]: 'Again',
  [Rating.Hard]: 'Hard',
  [Rating.Good]: 'Good',
  [Rating.Easy]: 'Easy',
}
