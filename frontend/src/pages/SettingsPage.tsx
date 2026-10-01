import { useEffect, useState } from 'react'
import { api, describeApiError } from '../api/client'
import { logError, logInfo } from '../api/logger'
import { Icon } from '../components/Icon'
import { DEFAULT_LLM_BASE_URL, LLM_MODELS, type LlmSettings, type UpdateInfo } from '../types'

const initialSettings: LlmSettings = {
  base_url: DEFAULT_LLM_BASE_URL,
  model: LLM_MODELS[0],
  api_key_configured: false,
  timeout_seconds: 60,
  temperature: 0.2,
}

const modelDescriptions: Record<(typeof LLM_MODELS)[number], string> = {
  'qwen3.5-plus': '通用推理与工具调用',
  'deepseek-v4-pro': '复杂协议诊断与代码分析',
  'deepseek-v4-flash': '低延迟实时问答',
  'glm-5.2': '中文工程知识与长上下文',
  'deepseek-v4.1-flash': '快速故障归因与摘要',
}

export function SettingsPage({ currentVersion }: { currentVersion?: string } = {}) {
  const [settings, setSettings] = useState<LlmSettings>(initialSettings)
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [checkingUpdate, setCheckingUpdate] = useState(false)
  const [stagingUpdate, setStagingUpdate] = useState(false)
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null)
  const [notice, setNotice] = useState<{ tone: 'success' | 'error' | 'warning'; text: string } | null>(null)

  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        const result = await api.getLlmSettings()
        if (!active) return
        setSettings({
          base_url: result.base_url || DEFAULT_LLM_BASE_URL,
          model: LLM_MODELS.includes(result.model as (typeof LLM_MODELS)[number]) ? result.model : LLM_MODELS[0],
          api_key_configured: Boolean(result.api_key_configured),
          timeout_seconds: result.timeout_seconds ?? 60,
          temperature: result.temperature ?? 0.2,
        })
      } catch (error) {
        logError('读取模型配置失败', error)
        if (active) setNotice({ tone: 'warning', text: '后端不可达，当前显示安全默认值；保存前请确认服务已启动。' })
      } finally {
        if (active) setLoading(false)
      }
    }
    void load()
    return () => { active = false }
  }, [])

  const save = async () => {
    setSaving(true)
    setNotice(null)
    try {
      const payload: LlmSettings & { api_key?: string } = {
        base_url: settings.base_url.trim(),
        model: settings.model,
        timeout_seconds: settings.timeout_seconds,
        temperature: settings.temperature,
      }
      if (apiKey) payload.api_key = apiKey
      const result = await api.updateLlmSettings(payload)
      setSettings((current) => ({
        ...current,
        api_key_configured: result.api_key_configured ?? (current.api_key_configured || Boolean(apiKey)),
      }))
      setApiKey('')
      setShowKey(false)
      setNotice({ tone: 'success', text: '模型配置已安全保存。API Key 已提交到后端，不会在界面中回显。' })
      logInfo('模型配置保存完成', { model: payload.model, baseUrl: payload.base_url, credentialUpdated: Boolean(apiKey) })
    } catch (error) {
      logError('保存模型配置失败', error, { model: settings.model, baseUrl: settings.base_url })
      setNotice({ tone: 'error', text: `保存失败：${describeApiError(error)}` })
    } finally {
      setSaving(false)
    }
  }

  const checkUpdate = async () => {
    setCheckingUpdate(true)
    try {
      const result = await api.checkUpdate()
      setUpdateInfo(result)
      logInfo('在线更新检查完成', { currentVersion: result.current_version, latestVersion: result.latest_version, available: result.available })
    } catch (error) {
      logError('在线更新检查失败', error)
      setNotice({ tone: 'error', text: `检查更新失败：${describeApiError(error)}` })
    } finally {
      setCheckingUpdate(false)
    }
  }

  const testConnection = async () => {
    setTesting(true)
    setNotice(null)
    try {
      const payload: LlmSettings & { api_key?: string } = {
        base_url: settings.base_url.trim(),
        model: settings.model,
        timeout_seconds: settings.timeout_seconds,
        temperature: settings.temperature,
      }
      if (apiKey) payload.api_key = apiKey
      const saved = await api.updateLlmSettings(payload)
      const result = await api.testLlmSettings()
      setSettings((current) => ({ ...current, api_key_configured: saved.api_key_configured ?? (current.api_key_configured || Boolean(apiKey)) }))
      setApiKey('')
      setShowKey(false)
      setNotice({ tone: 'success', text: `模型网关连接成功：${result.reply || 'OK'}` })
      logInfo('模型网关连接测试成功', { model: payload.model, baseUrl: payload.base_url })
    } catch (error) {
      logError('模型网关连接测试失败', error, { model: settings.model, baseUrl: settings.base_url })
      setNotice({ tone: 'error', text: `连接测试失败：${describeApiError(error)}` })
    } finally {
      setTesting(false)
    }
  }

  const stageUpdate = async () => {
    setStagingUpdate(true)
    setNotice(null)
    try {
      const result = await api.installUpdate()
      setNotice({ tone: 'success', text: `已开始升级至 v${result.version}，应用将自动重启，请稍候。` })
      logInfo('在线升级已启动', { version: result.version })
      const expected = result.version
      const deadline = Date.now() + 90_000
      const waitForRestart = async () => {
        if (Date.now() > deadline) {
          setNotice({ tone: 'warning', text: '重启尚未完成，请查看升级日志；安装失败时会恢复旧版本。' })
          return
        }
        try {
          const info = await api.health()
          if (info.status === 'ok' && info.version === expected) {
            window.location.reload()
            return
          }
        } catch (error) {
          logInfo('升级重启期间等待服务恢复', { detail: describeApiError(error) })
          // 重启期间连接中断属于预期状态，继续等待版本确认。
        }
        window.setTimeout(() => void waitForRestart(), 2000)
      }
      window.setTimeout(() => void waitForRestart(), 2000)
    } catch (error) {
      logError('在线升级失败', error)
      setNotice({ tone: 'error', text: `在线升级失败：${describeApiError(error)}` })
    } finally {
      setStagingUpdate(false)
    }
  }

  return (
    <div className="page settings-page">
      {notice && <div className={`inline-notice ${notice.tone}`}><Icon name="info" />{notice.text}<button onClick={() => setNotice(null)}><Icon name="x" size={14} /></button></div>}
      <section className="settings-layout">
        <div className="settings-main">
          <section className="panel settings-section">
            <div className="settings-section-header">
              <span className="settings-icon"><Icon name="bot" /></span>
              <div><span className="panel-kicker">OPENAI COMPATIBLE</span><h2>智能体模型</h2><p>配置兼容 OpenAI Chat Completions 格式的企业模型网关。</p></div>
              {settings.api_key_configured && <span className="configured-badge"><Icon name="shield" size={14} />凭证已配置</span>}
            </div>

            {loading ? <div className="settings-loading"><span className="spinner" />正在读取安全配置…</div> : (
              <div className="settings-form">
                <label className="form-field"><span>API Base URL <em>必填</em></span><div className="input-with-icon"><Icon name="network" size={16} /><input value={settings.base_url} onChange={(event) => setSettings((current) => ({ ...current, base_url: event.target.value }))} spellCheck={false} /></div><small>请求由后端代理发出，浏览器不会直接携带凭证。</small></label>
                <label className="form-field"><span>API Key {settings.api_key_configured ? <i>留空则保持现有凭证</i> : <em>尚未配置</em>}</span><div className="input-with-icon"><Icon name="shield" size={16} /><input type={showKey ? 'text' : 'password'} value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={settings.api_key_configured ? '••••••••••••••••（已安全保存）' : '输入 API Key'} autoComplete="new-password" /><button type="button" className="icon-button" onClick={() => setShowKey((value) => !value)} aria-label={showKey ? '隐藏密钥' : '显示密钥'}><Icon name={showKey ? 'eyeOff' : 'eye'} size={16} /></button></div><small>仅在保存时提交，读取配置时后端应只返回是否已配置。</small></label>

                <fieldset className="model-picker"><legend>模型</legend><div className="model-grid">
                  {LLM_MODELS.map((model) => (
                    <label key={model} className={settings.model === model ? 'selected' : ''}>
                      <input type="radio" name="model" value={model} checked={settings.model === model} onChange={() => setSettings((current) => ({ ...current, model }))} />
                      <span className="model-radio"><i /></span><div><strong>{model}</strong><small>{modelDescriptions[model]}</small></div>{settings.model === model && <Icon name="check" size={15} />}
                    </label>
                  ))}
                </div></fieldset>

                <div className="advanced-settings">
                  <label><span>请求超时</span><div className="input-unit"><input type="number" min={5} max={300} value={settings.timeout_seconds} onChange={(event) => setSettings((current) => ({ ...current, timeout_seconds: Number(event.target.value) }))} /><i>秒</i></div></label>
                  <label><span>Temperature</span><input type="number" min={0} max={2} step={0.1} value={settings.temperature} onChange={(event) => setSettings((current) => ({ ...current, temperature: Number(event.target.value) }))} /></label>
                </div>
                <div className="settings-actions"><button className="button secondary" type="button" onClick={() => { setSettings(initialSettings); setApiKey('') }}>恢复默认</button><button className="button ghost" onClick={() => void testConnection()} disabled={saving || testing || !settings.base_url.trim() || (!settings.api_key_configured && !apiKey)}>{testing ? <span className="spinner" /> : <Icon name="activity" />}{testing ? '测试中…' : '保存并测试'}</button><button className="button primary" onClick={() => void save()} disabled={saving || testing || !settings.base_url.trim()}>{saving ? <span className="spinner" /> : <Icon name="check" />}{saving ? '正在保存…' : '保存配置'}</button></div>
              </div>
            )}
          </section>

          <section className="panel settings-section updater-section">
            <div className="settings-section-header"><span className="settings-icon purple"><Icon name="refresh" /></span><div><span className="panel-kicker">APPLICATION UPDATE</span><h2>版本与在线升级</h2><p>发行版通过签名更新包进行安全升级，失败时恢复旧版本。</p></div><span className="version-chip">{updateInfo?.current_version || currentVersion ? `v${updateInfo?.current_version ?? currentVersion}` : '版本未知'}</span></div>
            <div className="update-row"><div><strong>更新来源</strong><span>由后端 HTTPS 发布清单与验签公钥配置，点击检查后确认可用版本。</span></div></div>
            <div className={`update-status${updateInfo?.available ? ' available' : ''}`}>
              <span><Icon name={updateInfo?.available ? 'arrowDown' : updateInfo?.latest_version ? 'check' : 'info'} />{updateInfo ? (updateInfo.available ? `发现新版本 v${updateInfo.latest_version ?? '—'}` : updateInfo.latest_version ? '当前已是最新版本' : '尚未配置更新源') : '尚未检查更新'}</span>
              <small>{updateInfo?.latest_version ? `签名状态：${updateInfo.signature_verified ? '已验证' : '未验证'}` : updateInfo ? '请在后端配置 HTTPS 更新清单与 Ed25519 公钥' : '点击按钮向更新服务查询'}</small>
              <button className="button ghost" disabled={checkingUpdate} onClick={() => void checkUpdate()}>{checkingUpdate ? <span className="spinner" /> : <Icon name="refresh" />}{checkingUpdate ? '检查中…' : '检查更新'}</button>
            </div>
            {updateInfo?.available && <div className="update-detail"><strong>更新说明</strong><p>{updateInfo.release_notes || '服务未提供更新说明。'}</p><span><Icon name={updateInfo.signature_verified ? 'shield' : 'info'} size={14} />{updateInfo.signature_verified ? '更新包签名已验证' : '更新包签名尚未验证，请勿安装'}</span>{updateInfo.signature_verified && <button className="button primary" disabled={stagingUpdate} onClick={() => void stageUpdate()}>{stagingUpdate ? <span className="spinner" /> : <Icon name="download" />}{stagingUpdate ? '下载并安装中…' : '升级到最新版本'}</button>}</div>}
          </section>
        </div>

        <aside className="settings-aside">
          <section className="panel security-card"><span className="security-illustration"><Icon name="shield" size={28} /></span><h3>凭证安全</h3><p>API Key 不写入前端源码、不保存到浏览器，也不会从配置接口回显。</p><ul><li><Icon name="check" />优先写入操作系统凭据库</li><li><Icon name="check" />日志不记录密钥</li><li><Icon name="check" />仅服务进程可读取</li></ul></section>
          <section className="panel runtime-card"><div className="panel-header"><div><span className="panel-kicker">RUNTIME</span><h3>运行环境</h3></div></div><dl><dt>前端</dt><dd>React + Vite</dd><dt>发行目标</dt><dd>Linux（当前验收目标）</dd><dt>更新源</dt><dd>后端配置 HTTPS 清单</dd><dt>配置协议</dt><dd>OpenAI Compatible</dd></dl></section>
        </aside>
      </section>
    </div>
  )
}
