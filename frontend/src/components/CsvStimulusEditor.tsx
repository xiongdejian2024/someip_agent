import { useState } from 'react'
import { logError, logInfo } from '../api/logger'

interface Props { value: string; onChange: (text: string) => void; disabled: boolean }

export function CsvStimulusEditor({ value, onChange, disabled }: Props) {
  const [error, setError] = useState('')
  const [reading, setReading] = useState(false)
  return <div>
    <label>导入 CSV 激励文件<input aria-label="导入 CSV 激励文件" type="file" accept=".csv,text/csv" disabled={disabled || reading} onChange={event => {
      const file = event.target.files?.[0]
      event.currentTarget.value = ''
      if (!file) return
      setReading(true); setError('')
      void (async () => {
        try {
          if (file.size > 1048576) throw new Error('CSV 文件不能超过 1 MiB')
          const text = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer())
          onChange(text)
          logInfo('已载入 CSV 文本草案，尚未执行', { filename: file.name, bytes: file.size })
        } catch (exception) {
          logError('读取 CSV 激励文件失败', exception, { filename: file.name })
          setError(exception instanceof Error ? exception.message : String(exception))
        } finally { setReading(false) }
      })()
    }} /></label>
    <label>CSV 时间轴（可选）<textarea aria-label="CSV 时间轴" value={value} maxLength={1048576} rows={6} spellCheck={false} disabled={disabled || reading} onChange={event => { setError(''); onChange(event.target.value) }} /></label>
    {error && <p role="alert">{error}；未更改有效 CSV 配置。</p>}
    <p>首列 time_ms 为从 0 开始严格递增的整型毫秒；其余列为 JSON Pointer，根标量用空列名。每格为 JSON 标量，字符串按 CSV 引号规则转义。示例：<code>{'time_ms,/tag,/nested/temperature\n0,9,-3\n29,10,4'}</code>。</p>
    <p>行间保持前值，末行后保持末值，不插值、不循环。发送按周期采样同一事件逻辑时间，不保证每一 CSV 行都发出；文件最多 1 MiB、8192 行、128 个信号，预编译 IPC 最多 2 MiB。其他路径仍可配置 JSON 激励，不能重复绑定；只载入文本，不执行文件或自动发包。</p>
  </div>
}
