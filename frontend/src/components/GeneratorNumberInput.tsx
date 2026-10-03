import { useEffect, useRef, useState } from 'react'
import { logError } from '../api/logger'
import { parseGeneratorValue, type GeneratorDataType, type GeneratorNumber } from '../data/generatorValues'

interface Props {
  label: string
  value: GeneratorNumber
  dataType: GeneratorDataType
  disabled: boolean
  onChange: (value: GeneratorNumber) => void
  onValidityChange: (valid: boolean) => void
}

export function GeneratorNumberInput({ label, value, dataType, disabled, onChange, onValidityChange }: Props) {
  const [text, setText] = useState(String(value))
  const [error, setError] = useState<string | null>(null)
  const validityCallback = useRef(onValidityChange)
  validityCallback.current = onValidityChange
  useEffect(() => () => validityCallback.current(true), [])
  useEffect(() => { setText(String(value)); setError(null); validityCallback.current(true) }, [value])
  return <label className="sim-form-field"><span>{label}</span><input type="text" inputMode="decimal" aria-label={label} aria-invalid={!!error}
    value={text} disabled={disabled} spellCheck={false} onChange={event => {
      const next = event.target.value
      setText(next)
      try {
        const parsed = parseGeneratorValue(next, dataType)
        setError(null); onValidityChange(true); onChange(parsed)
      } catch (exception) {
        logError('激励数值输入校验失败', exception, { field: label, dataType })
        setError(exception instanceof Error ? exception.message : String(exception))
        onValidityChange(false)
      }
    }} />{error && <small role="alert">{error}；无效编辑不写入工程草案，也不会用于启动。</small>}</label>
}
