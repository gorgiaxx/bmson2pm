const DEFAULT_PEAK_COUNT = 2400

function decodedBufferBytes(buffer: AudioBuffer): number {
  return buffer.length * Math.max(1, buffer.numberOfChannels) * Float32Array.BYTES_PER_ELEMENT
}

export class DecodedAudioBufferCache {
  private readonly entries = new Map<string, { buffer: AudioBuffer; bytes: number }>()
  private bytes = 0

  constructor(private readonly maxBytes: number) {}

  get sizeBytes(): number {
    return this.bytes
  }

  get(url: string): AudioBuffer | null {
    const entry = this.entries.get(url)
    if (!entry) return null
    this.entries.delete(url)
    this.entries.set(url, entry)
    return entry.buffer
  }

  set(url: string, buffer: AudioBuffer): void {
    const previous = this.entries.get(url)
    if (previous) {
      this.bytes -= previous.bytes
      this.entries.delete(url)
    }
    const bytes = decodedBufferBytes(buffer)
    this.entries.set(url, { buffer, bytes })
    this.bytes += bytes
    while (this.bytes > this.maxBytes && this.entries.size > 1) {
      const oldest = this.entries.entries().next().value as [string, { buffer: AudioBuffer; bytes: number }] | undefined
      if (!oldest) break
      this.entries.delete(oldest[0])
      this.bytes -= oldest[1].bytes
    }
  }

  clear(): void {
    this.entries.clear()
    this.bytes = 0
  }
}

export function buildAudioPeaks(buffer: AudioBuffer, peakCount = DEFAULT_PEAK_COUNT): Float32Array {
  const samples = buffer.getChannelData(0)
  const count = Math.max(1, Math.round(peakCount))
  const block = Math.max(1, Math.ceil(samples.length / count))
  const peaks = new Float32Array(count)
  for (let index = 0; index < count; index += 1) {
    let peak = 0
    const start = index * block
    const end = Math.min(start + block, samples.length)
    for (let sample = start; sample < end; sample += 1) {
      peak = Math.max(peak, Math.abs(samples[sample]))
    }
    peaks[index] = peak
  }
  return peaks
}
