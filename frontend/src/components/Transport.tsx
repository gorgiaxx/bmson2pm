import { FastForward, Gauge, Music, Pause, Play, Repeat2, Rewind, Square, Volume2, VolumeX } from 'lucide-react'
import { formatTime } from '../timing'

interface TransportProps {
  playing: boolean
  position: number
  duration: number
  speed: number
  loop: boolean
  musicMuted: boolean
  audioName: string | null
  onPlay: () => void
  onPause: () => void
  onStop: () => void
  onSeek: (seconds: number) => void
  onSpeed: (speed: number) => void
  onLoop: (loop: boolean) => void
  onMute: (muted: boolean) => void
  onAudioFile: (file: File) => void
}

export function Transport(props: TransportProps) {
  return (
    <footer className="transport">
      <div className="transport-buttons">
        <button type="button" className="icon-button" onClick={() => props.onSeek(props.position - 5)} title="后退 5 秒"><Rewind size={16} /></button>
        <button type="button" className="play-button" onClick={props.playing ? props.onPause : props.onPlay} title={props.playing ? '暂停' : '播放'}>
          {props.playing ? <Pause size={18} fill="currentColor" /> : <Play size={18} fill="currentColor" />}
        </button>
        <button type="button" className="icon-button" onClick={props.onStop} title="停止"><Square size={14} fill="currentColor" /></button>
        <button type="button" className="icon-button" onClick={() => props.onSeek(props.position + 5)} title="前进 5 秒"><FastForward size={16} /></button>
      </div>
      <div className="time-readout"><strong>{formatTime(props.position)}</strong><span>/</span><span>{formatTime(props.duration)}</span></div>
      <input
        className="transport-scrubber"
        type="range"
        min="0"
        max={Math.max(props.duration, 1)}
        step="0.01"
        value={Math.min(props.position, props.duration)}
        onChange={(event) => props.onSeek(Number(event.target.value))}
        aria-label="播放位置"
      />
      <label className="audio-loader" title="载入音乐文件">
        <Music size={14} />
        <span>{props.audioName ?? '载入音乐'}</span>
        <input type="file" accept="audio/*,.flac,.ogg,.wav,.mp3" hidden onChange={(event) => {
          const file = event.target.files?.[0]
          if (file) props.onAudioFile(file)
          event.currentTarget.value = ''
        }} />
      </label>
      <button type="button" className={`icon-button ${props.loop ? 'active' : ''}`} onClick={() => props.onLoop(!props.loop)} title="循环播放"><Repeat2 size={16} /></button>
      <label className="speed-control" title="播放速度"><Gauge size={15} />
        <select value={props.speed} onChange={(event) => props.onSpeed(Number(event.target.value))}>
          {[0.5, 0.75, 1, 1.25, 1.5].map((speed) => <option key={speed} value={speed}>{speed.toFixed(2)}×</option>)}
        </select>
      </label>
      <button type="button" className={`icon-button ${props.musicMuted ? 'active' : ''}`} onClick={() => props.onMute(!props.musicMuted)} title={props.musicMuted ? '取消音乐静音' : '音乐静音'}>
        {props.musicMuted ? <VolumeX size={17} /> : <Volume2 size={17} />}
      </button>
    </footer>
  )
}
