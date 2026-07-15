import { useCallback, useState } from 'react'
import { buildAudioPeaks } from '../audio'

export interface LoadedAudio {
  name: string
  buffer: AudioBuffer
  peaks: Float32Array
}

export function useAudioFile() {
  const [audio, setAudio] = useState<LoadedAudio | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(async (file: File) => {
    setError('')
    try {
      const context = new AudioContext({ latencyHint: 'interactive' })
      const buffer = await context.decodeAudioData(await file.arrayBuffer())
      const peaks = buildAudioPeaks(buffer)
      await context.close()
      setAudio({ name: file.name, buffer, peaks })
      return buffer
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '音频解码失败'
      setError(message)
      throw reason
    }
  }, [])

  const clear = useCallback(() => {
    setAudio(null)
    setError('')
  }, [])

  return { audio, error, load, clear }
}
