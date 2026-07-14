/**
 * UUIDv7 — time-ordered, so review ids sort by creation and Postgres keeps the
 * review_logs table roughly insert-ordered on disk.
 *
 * crypto.randomUUID() is v4: random, and would scatter inserts across the index. The
 * layout is 48 bits of Unix milliseconds, 4 bits version, 12 bits random, 2 bits
 * variant, 62 bits random.
 */
export function uuidv7(now: number = Date.now()): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)

  const view = new DataView(bytes.buffer)
  // 48-bit millisecond timestamp: high 32 bits, then low 16.
  view.setUint32(0, Math.floor(now / 0x1_0000))
  view.setUint16(4, now % 0x1_0000)

  view.setUint8(6, (view.getUint8(6) & 0x0f) | 0x70) // version 7
  view.setUint8(8, (view.getUint8(8) & 0x3f) | 0x80) // RFC 4122 variant

  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join('-')
}
