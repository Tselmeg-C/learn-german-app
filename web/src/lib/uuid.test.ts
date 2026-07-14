import { describe, expect, it } from 'vitest'

import { uuidv7 } from './uuid'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/

describe('uuidv7', () => {
  it('looks like a uuid', () => {
    expect(uuidv7()).toMatch(UUID_RE)
  })

  it('declares version 7 and the RFC 4122 variant', () => {
    const id = uuidv7()
    expect(id[14]).toBe('7')
    expect(['8', '9', 'a', 'b']).toContain(id[19])
  })

  it('is unique across a tight loop', () => {
    const ids = new Set(Array.from({ length: 5000 }, () => uuidv7()))
    expect(ids.size).toBe(5000)
  })

  it('sorts lexicographically by time', () => {
    // The reason for v7 over v4: ids generated later must sort later, so review_logs
    // stays roughly insert-ordered in Postgres.
    const early = uuidv7(new Date('2026-01-01T00:00:00Z').getTime())
    const middle = uuidv7(new Date('2026-06-01T00:00:00Z').getTime())
    const late = uuidv7(new Date('2026-12-01T00:00:00Z').getTime())
    expect([late, early, middle].sort()).toEqual([early, middle, late])
  })

  it('encodes the timestamp it was given', () => {
    const at = new Date('2026-07-14T09:00:00Z').getTime()
    const millis = Number.parseInt(uuidv7(at).replace(/-/g, '').slice(0, 12), 16)
    expect(millis).toBe(at)
  })
})
