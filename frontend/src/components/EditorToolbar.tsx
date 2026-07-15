import {
  Clipboard, Copy, FlipHorizontal2, Magnet, MousePointer2, Pencil, Redo2,
  ScanLine, Trash2, Undo2, ZoomIn, ZoomOut,
} from 'lucide-react'
import { QUANTIZATIONS } from '../constants'
import { useEditorStore } from '../store'

interface EditorToolbarProps { currentPulse: number }

export function EditorToolbar({ currentPulse }: EditorToolbarProps) {
  const tool = useEditorStore((state) => state.tool)
  const setTool = useEditorStore((state) => state.setTool)
  const quantize = useEditorStore((state) => state.quantizeDivisor)
  const setQuantize = useEditorStore((state) => state.setQuantizeDivisor)
  const zoom = useEditorStore((state) => state.zoom)
  const setZoom = useEditorStore((state) => state.setZoom)
  const selected = useEditorStore((state) => state.selectedIds.size)
  const undoStack = useEditorStore((state) => state.undoStack)
  const redoStack = useEditorStore((state) => state.redoStack)
  const clipboard = useEditorStore((state) => state.clipboard)
  const undo = useEditorStore((state) => state.undo)
  const redo = useEditorStore((state) => state.redo)
  const copy = useEditorStore((state) => state.copySelected)
  const paste = useEditorStore((state) => state.pasteAt)
  const duplicate = useEditorStore((state) => state.duplicateSelected)
  const mirror = useEditorStore((state) => state.mirrorSelected)
  const quantizeSelected = useEditorStore((state) => state.quantizeSelected)
  const remove = useEditorStore((state) => state.deleteSelected)

  return (
    <div className="editor-toolbar">
      <div className="segmented" aria-label="编辑工具">
        <button type="button" className={tool === 'select' ? 'active' : ''} onClick={() => setTool('select')} title="选择工具 (V)"><MousePointer2 size={15} /></button>
        <button type="button" className={tool === 'draw' ? 'active' : ''} onClick={() => setTool('draw')} title="绘制工具 (P)"><Pencil size={15} /></button>
      </div>
      <span className="toolbar-divider" />
      <button type="button" className="icon-button" disabled={!undoStack.length} onClick={undo} title="撤销 (Cmd/Ctrl+Z)"><Undo2 size={16} /></button>
      <button type="button" className="icon-button" disabled={!redoStack.length} onClick={redo} title="重做 (Cmd/Ctrl+Shift+Z)"><Redo2 size={16} /></button>
      <span className="toolbar-divider" />
      <button type="button" className="icon-button" disabled={!selected} onClick={copy} title="复制"><Copy size={15} /></button>
      <button type="button" className="icon-button" disabled={!clipboard.length} onClick={() => paste(currentPulse)} title="粘贴到播放头"><Clipboard size={15} /></button>
      <button type="button" className="icon-button" disabled={!selected} onClick={duplicate} title="重复"><ScanLine size={15} /></button>
      <button type="button" className="icon-button" disabled={!selected} onClick={mirror} title="左右小鼓镜像"><FlipHorizontal2 size={15} /></button>
      <button type="button" className="icon-button danger-hover" disabled={!selected} onClick={remove} title="删除"><Trash2 size={15} /></button>
      <span className="toolbar-spacer" />
      <label className="quantize-control"><Magnet size={14} /><span>吸附</span>
        <select value={quantize} onChange={(event) => setQuantize(Number(event.target.value))}>
          {QUANTIZATIONS.map((item) => <option key={item.divisor} value={item.divisor}>{item.label}</option>)}
        </select>
      </label>
      <button type="button" className="icon-button" onClick={quantizeSelected} disabled={!selected} title="量化所选音符"><Magnet size={15} /></button>
      <span className="toolbar-divider" />
      <button type="button" className="icon-button" onClick={() => setZoom(zoom / 1.16)} title="缩小时间轴"><ZoomOut size={16} /></button>
      <span className="zoom-value">{Math.round(zoom / 88 * 100)}%</span>
      <button type="button" className="icon-button" onClick={() => setZoom(zoom * 1.16)} title="放大时间轴"><ZoomIn size={16} /></button>
    </div>
  )
}
