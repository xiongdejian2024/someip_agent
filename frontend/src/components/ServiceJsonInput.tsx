import { useEffect, useId, useMemo, useRef } from 'react'
import { parseJson, stringifyJson } from '../api/json'
import { logError, logInfo } from '../api/logger'

interface Props {
  label: string; ariaLabel: string; value: string; rows?: number; array?: boolean; disabled: boolean
  onChange: (text: string) => void; onValidityChange: (valid: boolean) => void
}

/** 只检查 JSON 语法／数组外形；业务类型继续由冻结 ARXML 和原生 Codec 校验。 */
export function ServiceJsonInput({ label, ariaLabel, value, rows = 5, array = false, disabled, onChange, onValidityChange }: Props) {
  const id = useId()
  const callback = useRef(onValidityChange)
  callback.current = onValidityChange
  const error = useMemo(() => {
    try {
      const parsed = parseJson(value)
      if (array && !Array.isArray(parsed)) throw new Error('激励绑定必须是 JSON 数组；固定参数请填 []')
      return ''
    } catch (exception) {
      logError('服务 JSON 草案校验失败', exception, { field: ariaLabel })
      return exception instanceof Error ? exception.message : String(exception)
    }
  }, [value, array, ariaLabel])
  useEffect(() => { callback.current(!error) }, [error])
  return <div className="service-json-input">
    <div className="service-json-heading"><label htmlFor={id}>{label}</label><button type="button" aria-label={`格式化${ariaLabel}`} className="sim-text-button" disabled={disabled || !!error} onClick={() => {
      try { onChange(stringifyJson(parseJson(value), 2)); logInfo('已格式化服务 JSON 草案，未执行', { field: ariaLabel }) }
      catch (exception) { logError('格式化服务 JSON 草案失败', exception, { field: ariaLabel }) }
    }}>格式化 JSON</button></div>
    <textarea id={id} aria-label={ariaLabel} aria-invalid={!!error} aria-describedby={id + '-hint'} value={value} rows={rows} disabled={disabled} spellCheck={false} onChange={event => onChange(event.target.value)} />
    <small id={id + '-hint'} role={error ? 'alert' : undefined} className={error ? 'service-json-error' : 'muted'}>{error ? `${error}；已禁用执行，原生任务不会因编辑而更新。` : '仅编辑草案；大整数使用十进制数字，不加引号。业务布局由 ARXML 校验。'}</small>
  </div>
}
