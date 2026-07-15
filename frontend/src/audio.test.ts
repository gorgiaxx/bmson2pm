import { describe, expect, it } from 'vitest'
import { DecodedAudioBufferCache } from './audio'

function buffer(length: number, numberOfChannels = 1): AudioBuffer {
  return { length, numberOfChannels } as AudioBuffer
}

describe('DecodedAudioBufferCache', () => {
  it('keeps decoded PCM in memory and evicts the least recently used resource', () => {
    const cache = new DecodedAudioBufferCache(100)
    const first = buffer(10)
    const second = buffer(20)
    cache.set('/first.wav', first)
    cache.set('/second.ogg', second)
    expect(cache.get('/first.wav')).toBeNull()
    expect(cache.get('/second.ogg')).toBe(second)
    expect(cache.sizeBytes).toBe(80)
  })

  it('refreshes recency when a decoded resource is reused', () => {
    const cache = new DecodedAudioBufferCache(100)
    const first = buffer(10)
    const second = buffer(10)
    cache.set('/first.wav', first)
    cache.set('/second.wav', second)
    expect(cache.get('/first.wav')).toBe(first)
    cache.set('/third.wav', buffer(10))
    expect(cache.get('/second.wav')).toBeNull()
    expect(cache.get('/first.wav')).toBe(first)
  })
})
