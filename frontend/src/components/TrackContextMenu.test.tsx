// @vitest-environment jsdom

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { DEFAULT_LANES, TRACK_COLOR_PALETTE } from '../constants'
import { TrackContextMenu } from './TrackContextMenu'

describe('TrackContextMenu', () => {
  it('offers the preset palette and applies a selected track color', () => {
    const onSetColor = vi.fn()
    render(
      <TrackContextMenu
        x={20}
        y={20}
        lane={DEFAULT_LANES[0]}
        lanes={DEFAULT_LANES}
        maxPulse={960}
        laneNoteCount={4}
        selectedCount={0}
        canRemove={false}
        onClose={() => undefined}
        onSelectLane={() => undefined}
        onCreateAndMove={() => undefined}
        onMove={() => undefined}
        onSwap={() => undefined}
        onMerge={() => undefined}
        onSetColor={onSetColor}
        onSetPm3Track={() => undefined}
        onDeleteSelected={() => undefined}
        onRemove={() => undefined}
      />,
    )

    const swatches = screen.getAllByRole('button', { name: /^颜色 / })
    expect(swatches).toHaveLength(TRACK_COLOR_PALETTE.length)
    expect(swatches).toHaveLength(24)
    fireEvent.click(screen.getByRole('button', { name: /#ef5350$/ }))
    expect(onSetColor).toHaveBeenCalledWith('#ef5350')
  })

  it('assigns an anonymous lane to an available PM3 auxiliary track', () => {
    const onSetPm3Track = vi.fn()
    const lane = {
      ...DEFAULT_LANES[0],
      id: 7,
      code: 'notelist_21',
      display_name: 'NoteList Track 21',
      kind: 'anonymous' as const,
      extensions: { notelist: { track_id: 21 } },
    }
    render(
      <TrackContextMenu
        x={20}
        y={20}
        lane={lane}
        lanes={[...DEFAULT_LANES, lane]}
        maxPulse={960}
        laneNoteCount={4}
        selectedCount={0}
        canRemove={false}
        onClose={() => undefined}
        onSelectLane={() => undefined}
        onCreateAndMove={() => undefined}
        onMove={() => undefined}
        onSwap={() => undefined}
        onMerge={() => undefined}
        onSetColor={() => undefined}
        onSetPm3Track={onSetPm3Track}
        onDeleteSelected={() => undefined}
        onRemove={() => undefined}
      />,
    )

    fireEvent.change(screen.getByLabelText('PM3 Track 分类'), { target: { value: '14' } })
    expect(onSetPm3Track).toHaveBeenCalledWith(14)
  })
})
