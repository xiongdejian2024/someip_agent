import { useEffect, useRef, useState } from 'react'
import { stringifyJson } from '../api/json'
import { logError } from '../api/logger'
import { parseTimedGraph, type TimedStateGraph } from '../data/timedStates'
import type { GeneratorDataType, GeneratorNumber } from '../data/generatorValues'
import type { TimedSignalState } from '../types'

interface Props {
  initialState: string; states: TimedSignalState[]; dataType: GeneratorDataType
  minimum: GeneratorNumber; maximum: GeneratorNumber; disabled: boolean
  onChange: (graph: TimedStateGraph) => void; onValidityChange: (valid: boolean) => void
}

export function TimedStateInput({ initialState, states, dataType, minimum, maximum, disabled, onChange, onValidityChange }: Props) {
  const [text, setText] = useState(() => stringifyJson({ initial_state: initialState, states }, 2))
  const [error, setError] = useState('')
  const callback = useRef(onValidityChange)
  callback.current = onValidityChange
  useEffect(() => () => callback.current(true), [])
  useEffect(() => { setText(stringifyJson({ initial_state: initialState, states }, 2)); setError(''); callback.current(true) }, [initialState, states])
  return <label className="sim-form-field"><span>时间状态图（JSON）</span>
    <textarea aria-label="时间状态图（JSON）" aria-invalid={!!error} rows={10} spellCheck={false} value={text} disabled={disabled} onChange={event => {
      setText(event.target.value)
      try { const graph = parseTimedGraph(event.target.value, dataType, minimum, maximum); setError(''); onValidityChange(true); onChange(graph) }
      catch (exception) { logError('时间状态图输入校验失败', exception, { dataType }); setError(exception instanceof Error ? exception.message : String(exception)); onValidityChange(false) }
    }} />
    {error && <small role="alert">{error}；无效编辑不写入工程，不用于启动。</small>}
    <small>每状态 name/value；定时转换需正整型 duration_ms 与 next，终态不设置二者并保持。允许循环，禁止零时长与不可达状态；最多 128 个。数值/时刻保留精确整数；布尔值用 true/false。本页用原生经过时间，完整事件用共享逻辑时间；不支持条件表达式或硬实时。</small>
  </label>
}
