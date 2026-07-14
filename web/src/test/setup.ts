import '@testing-library/jest-dom/vitest'
import 'fake-indexeddb/auto'

// jsdom has no crypto.getRandomValues, which uuidv7() needs.
import { webcrypto } from 'node:crypto'

if (!globalThis.crypto?.getRandomValues) {
  Object.defineProperty(globalThis, 'crypto', { value: webcrypto })
}
