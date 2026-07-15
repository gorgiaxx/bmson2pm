import { describe, expect, it } from 'vitest'
import { createDemoProject } from './demo'
import { activeBgaEvent, bgaResourceUrl, readBmsBga } from './bga'

describe('BMS BGA', () => {
  it('parses Base, Layer and Poor events with timing and MV offset', () => {
    const project = createDemoProject('bga-project')
    project.timing.initial_bpm = 120
    project.timing.resolution = 240
    project.timing.mv_offset_ms = 250
    project.mv_configuration.bms_bga = {
      bmp_defs: { '01': 'movie.avi', '02': 'layer.png' },
      assets: {
        '01': {
          id: '01', filename: 'movie.avi', kind: 'video',
          resource: {
            project_id: project.id, path: 'movie.avi', preview_path: '_bga_preview/movie.mp4', exists: true,
          },
        },
      },
      events: {
        base: [{ pulse: 0, bmp_id: '01', line: 10 }],
        layer: [{ pulse: 480, bmp_id: '02', line: 20 }],
        poor: [{ pulse: 960, bmp_id: '02', line: 30 }],
      },
    }
    const data = readBmsBga(project)
    expect(data?.events.base[0].time).toBe(0.25)
    expect(data?.events.layer[0].time).toBe(1.25)
    expect(data?.events.poor[0].time).toBe(2.25)
    expect(data?.resources.get('02')?.exists).toBe(false)
    expect(bgaResourceUrl(data!.resources.get('01')!)).toBe(
      '/api/projects/bga-project/bga-resource?path=_bga_preview%2Fmovie.mp4',
    )
  })

  it('selects the last visual event at or before the playhead', () => {
    const project = createDemoProject('bga-project')
    project.mv_configuration.bms_bga = {
      bmp_defs: { '01': 'a.png', '02': 'b.png' },
      events: {
        base: [{ pulse: 0, bmp_id: '01' }, { pulse: 240, bmp_id: '02' }],
        layer: [],
        poor: [],
      },
    }
    const events = readBmsBga(project)!.events.base
    expect(activeBgaEvent(events, -0.1)).toBeNull()
    expect(activeBgaEvent(events, 0)?.bmpId).toBe('01')
    expect(activeBgaEvent(events, 0.5)?.bmpId).toBe('02')
  })
})
