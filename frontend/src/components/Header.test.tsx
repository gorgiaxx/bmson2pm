// @vitest-environment jsdom

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Header } from './Header'

describe('Header import format menu', () => {
  it('keeps BMSON and NoteList JSON as explicit import choices', () => {
    const onImport = vi.fn()
    const { container } = render(
      <Header
        connected
        onImport={onImport}
        onExport={() => undefined}
        onNewProject={() => undefined}
        onResearch={() => undefined}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '导入谱面' }))
    expect(screen.getByRole('menuitem', { name: 'BMSON标准 BMSON JSON' })).toBeTruthy()
    const noteList = screen.getByRole('menuitem', {
      name: 'NoteList JSONTPB 与 samplelist/notelist',
    })
    expect(noteList).toBeTruthy()
    expect(screen.getByRole('menuitem', {
      name: '传统 BMS 目录谱面与 WAV / OGG 资源',
    })).toBeTruthy()
    expect(screen.getByRole('menuitem', {
      name: '单独 BMS 谱面稍后在映射界面补充资源',
    })).toBeTruthy()

    fireEvent.click(noteList)
    const input = container.querySelector<HTMLInputElement>('input[type="file"]:not([multiple])')
    expect(input).not.toBeNull()
    expect(input?.accept).toBe('.json,application/json')
    const file = new File(['{}'], 'chart.json', { type: 'application/json' })
    fireEvent.change(input as HTMLInputElement, { target: { files: [file] } })
    expect(onImport).toHaveBeenCalledWith([file], 'notelist')
  })
})
