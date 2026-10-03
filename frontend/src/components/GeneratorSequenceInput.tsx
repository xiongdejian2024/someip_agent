import { useEffect, useRef, useState } from 'react'
import { stringifyJson } from '../api/json'
import { logError } from '../api/logger'
import { parseGeneratorSequence, type GeneratorDataType, type GeneratorNumber } from '../data/generatorValues'

interface Props {
  value: GeneratorNumber[]
  dataType: GeneratorDataType
  minimum: GeneratorNumber
  maximum: GeneratorNumber
  disabled: boolean
  onChange: (value: GeneratorNumber[]) => void
  onValidityChange: (valid: boolean) => void
}

export function GeneratorSequenceInput({ value, dataType, minimum, maximum, disabled, onChange, onValidityChange }: Props) {
  const [text, setText] = useState(() => stringifyJson(value))
  const [error, setError] = useState<string | null>(null)
  const validityCallback = useRef(onValidityChange)
  validityCallback.current = onValidityChange
  useEffect(() => () => validityCallback.current(true), [])
  useEffect(() => { setText(stringifyJson(value)); setError(null); validityCallback.current(true) }, [value])
  return <label className="sim-form-field"><span>循环序列（JSON 数组）</span><textarea aria-label="循环序列（JSON 数组）" aria-invalid={!!error}
    rows={4} value={text} disabled={disabled} spellCheck={false} onChange={event => {
      const next = event.target.value
      setText(next)
      try {
        const sequence = parseGeneratorSequence(next, dataType, minimum, maximum)
        setError(null); onValidityChange(true); onChange(sequence)
      } catch (exception) {
        logError('激励序列输入校验失败', exception, { dataType })
        setError(exception instanceof Error ? exception.message : String(exception))
        onValidityChange(false)
      }
    }} />{error && <small role="alert">{error}；无效编辑不写入工程草案，也不会用于启动。</small>}
    <small>每次发送取下一项，到末尾后循环；最多 8192 项。64 位整数直接写十进制数字，不加引号。</small>
  </label>
}
