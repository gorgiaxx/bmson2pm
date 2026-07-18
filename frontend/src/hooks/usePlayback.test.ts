// @vitest-environment jsdom

import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createDemoProject } from '../demo'
import type { KeySoundAsset } from '../types'
import {
  createBmsBgmSchedule,
  createNotePlaybackSchedule,
  keySoundResourceUrl,
  mediaPlaybackWindow,
  pm3BackgroundAudioResource,
  prioritizeKeySoundAssets,
  rollHitPulses,
  selectBmsPrimaryEvent,
  usePlayback,
} from './usePlayback'

afterEach(() => vi.unstubAllGlobals())

function asset(path: string, rootId = 'game'): KeySoundAsset {
  return {
    id: 'key-1',
    name: 'hit',
    filename: path,
    lane_ids: [1],
    volume: 1,
    delay_ms: 0,
    tags: [],
    source: 'pm3',
    extensions: {
      pm3: { resource: { root_id: rootId, path, exists: true } },
    },
  }
}

function bmsAsset(path: string): KeySoundAsset {
  return {
    id: 'bms-key-1', name: '01.wav', filename: '01.wav', lane_ids: [1],
    volume: 1, delay_ms: 0, tags: [], source: 'bms',
    extensions: {
      bms: { id: '01', resource: { project_id: 'project-1', path, exists: true } },
    },
  }
}

describe('keySoundResourceUrl', () => {
  it('only creates URLs for the PM3 game note directory', () => {
    expect(keySoundResourceUrl(asset('media/sound/note/1_Ho.wav'))).toBe(
      '/api/pm3/key-sound?root=game&path=media%2Fsound%2Fnote%2F1_Ho.wav',
    )
    expect(keySoundResourceUrl(asset('media/sound/BG/BG_001.ogg'))).toBeNull()
    expect(keySoundResourceUrl(asset('media/sound/note/1_Ho.wav', 'rewrite'))).toBeNull()
    expect(keySoundResourceUrl(asset('media/sound/note/../BG/song.wav'))).toBeNull()
  })

  it('creates a project-scoped URL for imported BMS audio', () => {
    expect(keySoundResourceUrl(bmsAsset('keys/01.ogg'))).toBe(
      '/api/projects/project-1/key-sound?path=keys%2F01.ogg',
    )
    expect(keySoundResourceUrl(bmsAsset('../01.ogg'))).toBeNull()
  })

  it('creates a project-scoped URL for manually uploaded audio', () => {
    const manual = {
      ...bmsAsset('unused.ogg'),
      source: 'manual',
      extensions: {
        editor: {
          resource: {
            project_id: 'manual-project',
            path: 'key-sounds/manual-hit.wav',
            exists: true,
          },
        },
      },
    }
    expect(keySoundResourceUrl(manual)).toBe(
      '/api/projects/manual-project/key-sound?path=key-sounds%2Fmanual-hit.wav',
    )
  })
})

describe('BMS automatic audio', () => {
  it('builds a stable channel 01 schedule in chart time', () => {
    const project = createDemoProject('bms-project')
    project.timing.initial_bpm = 120
    project.timing.resolution = 240
    project.unknown_data.bms_bgm_objects = [
      { pulse: 480, value: '01', line: 20, position: '0', key_sound_id: 'long' },
      { pulse: 0, value: '02', line: 19, position: '0', key_sound_id: 'cut' },
    ]
    const schedule = createBmsBgmSchedule(project)
    expect(schedule.map((event) => [event.assetId, event.time])).toEqual([
      ['cut', 0],
      ['long', 1],
    ])
    expect(schedule[0].key).toContain('bms-bgm:19:0:02')
  })

  it('selects the longest channel 01 resource instead of assuming WAV01', () => {
    const project = createDemoProject('bms-project')
    project.unknown_data.bms_bgm_objects = [
      { pulse: 0, value: '01', line: 10, position: '0', key_sound_id: 'short-01' },
      { pulse: 240, value: '2A', line: 11, position: '1/4', key_sound_id: 'long-2a' },
    ]
    const schedule = createBmsBgmSchedule(project)
    const buffers = new Map<string, AudioBuffer>([
      ['short-01', { duration: 0.4 } as AudioBuffer],
      ['long-2a', { duration: 90 } as AudioBuffer],
    ])
    expect(selectBmsPrimaryEvent(schedule, buffers)?.assetId).toBe('long-2a')
    expect(selectBmsPrimaryEvent(schedule, new Map([
      ['short-01', { duration: 0.4 } as AudioBuffer],
    ]))).toBeNull()
  })

  it('starts future music later and seeks into music that is already active', () => {
    expect(mediaPlaybackWindow(0, 1.68, 119.67)).toEqual({ delay: 1.68, offset: 0 })
    expect(mediaPlaybackWindow(10, 1.68, 119.67)).toEqual({ delay: 0, offset: 8.32 })
    expect(mediaPlaybackWindow(121.35, 1.68, 119.67)).toBeNull()
  })

  it('promotes a decoded long channel 01 resource to automatic music state', async () => {
    const project = createDemoProject('bms-state-project')
    project.timing.initial_bpm = 120
    project.timing.resolution = 240
    project.key_sounds = [{
      ...bmsAsset('keys/full-mix.ogg'),
      id: 'full-mix',
      name: 'Full mix',
      volume: 0.75,
      delay_ms: 250,
    }]
    project.unknown_data.bms_bgm_objects = [
      { pulse: 240, value: '2A', line: 12, position: '1/4', key_sound_id: 'full-mix' },
    ]
    const samples = new Float32Array([0, 0.5, -0.25, 0])
    const buffer = {
      duration: 90,
      length: samples.length,
      numberOfChannels: 1,
      getChannelData: () => samples,
    } as unknown as AudioBuffer
    class FakeAudioContext {
      state: AudioContextState = 'running'
      currentTime = 0
      destination = {} as AudioDestinationNode
      decodeAudioData = vi.fn(async () => buffer)
      close = vi.fn(async () => { this.state = 'closed' })
    }
    vi.stubGlobal('AudioContext', FakeAudioContext)
    const fetchMock = vi.fn(async () => ({
      ok: true,
      arrayBuffer: async () => new ArrayBuffer(8),
    } as Response))
    vi.stubGlobal('fetch', fetchMock)

    const { result, unmount } = renderHook(() => usePlayback(project, 'hard', null))
    await waitFor(() => expect(result.current.autoMusic).not.toBeNull())

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/projects/project-1/key-sound?path=keys%2Ffull-mix.ogg',
    )
    expect(result.current.autoMusic).toMatchObject({
      assetId: 'full-mix',
      name: 'Full mix',
      buffer,
      startTime: 0.75,
      volume: 0.75,
    })
    expect(result.current.autoMusic?.eventKey).toContain('bms-bgm:12:1/4:2A')
    expect(result.current.autoMusic?.peaks).toHaveLength(2400)
    expect(result.current.autoMusicLoading).toBeNull()
    expect(result.current.musicStart).toBe(0.75)
    expect(result.current.musicDuration).toBe(90)
    unmount()
  })
})

describe('low-latency preload planning', () => {
  it('loads automatic audio and early chart sounds before unused assets', () => {
    const project = createDemoProject('priority-project')
    project.key_sounds = [
      { ...asset('media/sound/note/a.wav'), id: 'a' },
      { ...asset('media/sound/note/b.wav'), id: 'b' },
      { ...asset('media/sound/note/c.wav'), id: 'c' },
    ]
    project.difficulties.hard.notes = [
      { ...project.difficulties.hard.notes[0], id: 'later', pulse: 480, key_sound_id: 'a' },
      { ...project.difficulties.hard.notes[0], id: 'early', pulse: 120, key_sound_id: 'b' },
    ]
    project.unknown_data.bms_bgm_objects = [
      { pulse: 0, value: '03', line: 1, position: '0', key_sound_id: 'c' },
    ]
    expect(prioritizeKeySoundAssets(project, 'hard').map((item) => item.id)).toEqual(['c', 'b', 'a'])
  })

  it('maps the PM3 background event to a preloaded game audio resource', () => {
    const project = createDemoProject('pm3-project')
    project.metadata.import_format = 'pm3'
    project.timing.initial_bpm = 120
    project.timing.resolution = 24
    project.audio_assets = [{
      id: 'bg', name: 'PM3 Background Music', filename: 'media/sound/BG/BG_001.ogg',
      duration: 90, sample_rate: null, extensions: {},
    }]
    project.source_files = [{
      role: 'background-audio', root_id: 'game', path: 'media/sound/BG/BG_001.ogg', exists: true,
    }]
    project.difficulties.hard.extensions.pm3 = {
      background_events: [{ track: 16, tick: 12, wav_index: 1, volume: 64 }],
    }
    const resource = pm3BackgroundAudioResource(project, 'hard')
    expect(resource?.url).toBe(
      '/api/pm3/audio?root=game&path=media%2Fsound%2FBG%2FBG_001.ogg',
    )
    expect(resource?.startTime).toBeCloseTo(0.5)
    expect(resource?.volume).toBeCloseTo(64 / 127)
  })
})

describe('live lane mute', () => {
  it('updates the lane audio bus without pausing playback', async () => {
    const project = createDemoProject('live-mute-project')
    const laneId = project.lanes[0].id
    project.metadata.audio_duration = 30
    project.key_sounds = [{
      ...asset('media/sound/note/live.wav'),
      id: 'live-key',
      lane_ids: [laneId],
    }]
    project.lanes = project.lanes.map((lane) => lane.id === laneId
      ? { ...lane, default_key_sound_id: 'live-key', muted: false }
      : lane)
    project.difficulties.hard.notes = []

    const destination = {} as AudioDestinationNode
    const gains: Array<{
      gain: {
        value: number
        cancelScheduledValues: ReturnType<typeof vi.fn>
        setTargetAtTime: ReturnType<typeof vi.fn>
      }
      output: AudioNode | null
      disconnect: ReturnType<typeof vi.fn>
    }> = []
    class FakeAudioContext {
      state: AudioContextState = 'running'
      currentTime = 0
      sampleRate = 48_000
      destination = destination
      decodeAudioData = vi.fn(async () => ({
        duration: 1,
        length: 48_000,
        numberOfChannels: 1,
      } as AudioBuffer))
      createBuffer = vi.fn(() => ({ duration: 0, length: 1, numberOfChannels: 1 } as AudioBuffer))
      createBufferSource = vi.fn(() => ({
        buffer: null,
        playbackRate: { value: 1 },
        onended: null,
        connect: vi.fn((output: AudioNode) => output),
        start: vi.fn(),
        stop: vi.fn(),
      } as unknown as AudioBufferSourceNode))
      createGain = vi.fn(() => {
        const node = {
          gain: {
            value: 1,
            cancelScheduledValues: vi.fn(),
            setTargetAtTime: vi.fn(),
          },
          output: null as AudioNode | null,
          connect: vi.fn((output: AudioNode) => {
            node.output = output
            return output
          }),
          disconnect: vi.fn(),
        }
        gains.push(node)
        return node as unknown as GainNode
      })
      close = vi.fn(async () => { this.state = 'closed' })
    }
    vi.stubGlobal('AudioContext', FakeAudioContext)
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      arrayBuffer: async () => new ArrayBuffer(8),
    } as Response)))

    const { result, rerender, unmount } = renderHook(
      ({ currentProject }) => usePlayback(currentProject, 'hard', null),
      { initialProps: { currentProject: project } },
    )
    await waitFor(() => expect(result.current.keySoundStatus.ready).toBe(1))
    await act(async () => { await result.current.play() })
    act(() => result.current.triggerLane(laneId))

    const laneGain = gains.find((gain) => gain.output === destination)
    expect(laneGain?.gain.value).toBe(1)
    expect(result.current.playing).toBe(true)

    const mutedProject = {
      ...project,
      lanes: project.lanes.map((lane) => lane.id === laneId ? { ...lane, muted: true } : lane),
    }
    rerender({ currentProject: mutedProject })
    await waitFor(() => expect(laneGain?.gain.setTargetAtTime).toHaveBeenLastCalledWith(0, 0, 0.012))
    expect(result.current.playing).toBe(true)

    const unmutedProject = {
      ...mutedProject,
      lanes: mutedProject.lanes.map((lane) => lane.id === laneId ? { ...lane, muted: false } : lane),
    }
    rerender({ currentProject: unmutedProject })
    await waitFor(() => expect(laneGain?.gain.setTargetAtTime).toHaveBeenLastCalledWith(1, 0, 0.012))
    expect(result.current.playing).toBe(true)
    unmount()
  })
})

describe('long-note roll playback', () => {
  it('previews generic long notes 1.5 times faster than a 1/16-note baseline', () => {
    const project = createDemoProject('roll-project')
    project.timing.resolution = 240
    const note = {
      ...project.difficulties.hard.notes[0],
      id: 'generic-roll',
      pulse: 120,
      length: 480,
      extensions: {},
    }
    expect(rollHitPulses(note, project.timing.resolution)).toEqual([
      120, 160, 200, 240, 280, 320, 360, 400, 440, 480, 520, 560, 600,
    ])
  })

  it('uses the PM3 hold hit count as its baseline and keeps the accelerated hits in range', () => {
    const project = createDemoProject('pm3-roll-project')
    project.timing.resolution = 24
    project.timing.initial_bpm = 120
    project.timing.key_sound_offset_ms = 25
    const note = {
      ...project.difficulties.hard.notes[0],
      id: 'pm3-roll',
      pulse: 24,
      length: 24,
      extensions: { pm3: { event: { hold_number: 3 } } },
    }
    project.difficulties.hard.notes = [note]

    const schedule = createNotePlaybackSchedule(project, 'hard')
    expect(schedule.map((event) => event.pulse)).toEqual([24, 32, 40, 48])
    expect(schedule.map((event) => event.key)).toEqual([
      'note:pm3-roll:roll:0',
      'note:pm3-roll:roll:1',
      'note:pm3-roll:roll:2',
      'note:pm3-roll:roll:3',
    ])
    expect(schedule.map((event) => event.hitCount)).toEqual([4, 4, 4, 4])
    expect(schedule[0].time).toBeCloseTo(0.525)
    expect(schedule[1].time).toBeCloseTo(0.6916667)
    expect(schedule[2].time).toBeCloseTo(0.8583333)
    expect(schedule[3].time).toBeCloseTo(1.025)
    expect(schedule.at(-1)?.pulse).toBe(note.pulse + note.length)
  })

  it('keeps simultaneous notes as separate schedulable audio instances', () => {
    const project = createDemoProject('polyphony-project')
    const source = project.difficulties.hard.notes[0]
    project.difficulties.hard.notes = [
      { ...source, id: 'layer-a', pulse: 240, length: 0 },
      { ...source, id: 'layer-b', pulse: 240, length: 0 },
    ]
    const schedule = createNotePlaybackSchedule(project, 'hard')
    expect(schedule.map((event) => event.key)).toEqual(['note:layer-a', 'note:layer-b'])
    expect(schedule[0].time).toBe(schedule[1].time)
  })

  it('converts every roll hit through BPM and STOP aware timing', () => {
    const project = createDemoProject('stopped-roll-project')
    project.timing.resolution = 24
    project.timing.initial_bpm = 120
    project.timing.stop_events = [{ id: 'stop', pulse: 36, duration_pulses: 12 }]
    const source = project.difficulties.hard.notes[0]
    project.difficulties.hard.notes = [{
      ...source,
      id: 'stopped-roll',
      pulse: 24,
      length: 24,
      extensions: { pm3: { event: { hold_number: 3 } } },
    }]
    const schedule = createNotePlaybackSchedule(project, 'hard')
    expect(schedule[0].time).toBeCloseTo(0.5)
    expect(schedule[1].time).toBeCloseTo(2 / 3)
    expect(schedule[2].time).toBeCloseTo(13 / 12)
    expect(schedule[3].time).toBeCloseTo(1.25)
  })
})
