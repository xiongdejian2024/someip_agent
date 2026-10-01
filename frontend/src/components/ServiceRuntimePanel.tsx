import { useEffect, useState } from 'react'
import { api, describeApiError } from '../api/client'
import { logError, logInfo } from '../api/logger'
import { protocolId } from '../agent/workspace'
import type { NativeServiceRequestMessage, NativeServiceSession, ServiceDefinition } from '../types'
import { Icon } from './Icon'
import './ServiceRuntimePanel.css'

export function ServiceRuntimePanel({ selected, demo }: { selected?: ServiceDefinition; demo: boolean }) {
  const [role, setRole] = useState<'client' | 'server'>('client')
  const [transport, setTransport] = useState<'internal' | 'udp' | 'tcp'>('internal')
  const [byteOrder, setByteOrder] = useState<'big' | 'little'>('big')
  const [applicationId, setApplicationId] = useState('0x3401')
  const [peer, setPeer] = useState('10.77.0.2')
  const [port, setPort] = useState('30520')
  const [instance, setInstance] = useState('')
  const [includeInternalPeer, setIncludeInternalPeer] = useState(false)
  const [sessions, setSessions] = useState<NativeServiceSession[]>([])
  const [sessionId, setSessionId] = useState('')
  const [memberKey, setMemberKey] = useState('')
  const [action, setAction] = useState<'call' | 'notify' | 'respond'>('call')
  const [functionName, setFunctionName] = useState('')
  const [args, setArgs] = useState('{}')
  const [requestId, setRequestId] = useState('')
  const [timeout, setTimeout] = useState('5')
  const [pending, setPending] = useState<NativeServiceRequestMessage[]>([])
  const [results, setResults] = useState<Array<{ id: string; member: string; functionName: string; text: string }>>([])
  const [operations, setOperations] = useState<ReadonlySet<string>>(new Set())
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [pollError, setPollError] = useState('')
  const instances = selected?.instanceIds ?? (protocolId(selected?.instanceId) != null ? [protocolId(selected?.instanceId)!] : [])
  const session = sessions.find(item => item.id === sessionId)
  const member = session?.members.find(item => item.key === memberKey)
  const functions = action === 'notify' ? member?.events ?? [] : member?.methods ?? []
  const effectiveFunction = functions.includes(functionName) ? functionName : functions[0] ?? ''
  const commandKey = `command:${sessionId}:${memberKey}:${action}`
  const busy = operations.has('control')
  const commandBusy = operations.has(commandKey)
  const requestsBusy = operations.has(`requests:${sessionId}`)

  useEffect(() => { setInstance('') }, [selected?.id])
  useEffect(() => {
    let cancelled = false
    let polling = false
    let failed = false
    const refresh = async () => {
      if (polling) return
      polling = true
      try {
        const data = await api.serviceSessions()
        if (!cancelled) {
          setSessions(data); setPollError('')
          if (failed) logInfo('原生服务会话状态读取已恢复')
          failed = false
        }
      } catch (cause) {
        if (!cancelled) {
          if (!failed) logError('读取原生服务会话状态失败', cause)
          failed = true
          setPollError(`会话状态读取失败：${describeApiError(cause)}`)
        }
      } finally { polling = false }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 1500)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [])

  const run = async (operation: string, work: () => Promise<void>, key = 'control') => {
    setOperations(current => new Set([...current, key])); setError(''); setNotice('')
    try { await work(); logInfo(operation) }
    catch (cause) { logError(operation, cause); setError(describeApiError(cause)) }
    finally { setOperations(current => { const next = new Set(current); next.delete(key); return next }) }
  }

  const start = () => run('初始化原生服务会话', async () => {
    if (!selected?.path || !selected.deploymentPath || demo) throw new Error('请先导入真实且具有明确部署的 ARXML 服务')
    const instanceId = Number(instance || instances[0])
    const appId = Number(applicationId)
    if (!Number.isInteger(instanceId) || !instances.includes(instanceId)) throw new Error('请选择已部署的实例')
    if (!Number.isInteger(appId) || appId <= 0 || appId >= 0xffff) throw new Error('应用 ID 必须为 1 至 65534 的整数，可使用十六进制')
    const created = await api.startServiceSession({
      application_name: `web_${crypto.randomUUID().replaceAll('-', '')}`,
      application_id: appId,
      members: {
        [selected.name]: {
          service: selected.path, deployment_path: selected.deploymentPath, role, transport,
          instance_id: instanceId, byte_order: byteOrder,
          ...(transport === 'internal' ? {} : { peer_host: peer, port: Number(port) }),
        },
        ...(transport === 'internal' && includeInternalPeer ? {
          [`${selected.name}Peer`]: {
            service: selected.path, deployment_path: selected.deploymentPath,
            role: role === 'client' ? 'server' as const : 'client' as const,
            transport: 'internal' as const, instance_id: instanceId, byte_order: byteOrder,
          },
        } : {}),
      },
    })
    setSessions(current => [created, ...current]); setSessionId(created.id)
    setMemberKey(created.members[0]?.key ?? ''); setPending([])
    setAction(role === 'client' ? 'call' : 'notify')
    setNotice('原生进程与成员 socket 已初始化；服务是否在线以成员状态为准。')
  })

  const command = () => run('执行原生成员操作', async () => {
    if (!session?.running || !member || !effectiveFunction) throw new Error('请选择活动会话和接口')
    const seconds = Number(timeout)
    if (!Number.isFinite(seconds) || seconds <= 0 || seconds > 30) throw new Error('超时必须大于 0 且不超过 30 秒')
    const result = await api.serviceCommand(session.id, action, {
      member: member.key, function: effectiveFunction, args: JSON.parse(args), timeout: seconds,
      ...(action === 'respond' ? { request_id: Number(requestId) } : {}),
    })
    const text = result.status === 'responded' ? `收到真实方法响应：${JSON.stringify(result.result)}` : '原生已完成编码并提交；不代表远端接收或线上抓包验证。'
    setNotice(text)
    setResults(current => [{ id: crypto.randomUUID(), member: member.key, functionName: effectiveFunction, text }, ...current].slice(0, 8))
    if (action === 'respond') setPending(await api.serviceRequests(session.id))
  }, commandKey)

  return <section className="panel service-runtime-panel">
    <div className="panel-header"><div><span className="panel-kicker">原生服务运行时</span><h3>client / server 初始化与调用</h3></div><Icon name="network" /></div>
    <p className="muted">Python 字典配置 → 二进制 → 控制/成员 socket → vsomeip。默认内部隔离，不同内部会话互不通信；可在同一会话加入测试对端，业务响应仍需人工填写。在线模式需后端发送开关、目标白名单和本机网卡地址。应用 ID 必须独立，0x1101 保留给默认仿真。</p>
    <div className="service-runtime-form">
      <label>角色<select value={role} onChange={event => setRole(event.target.value as typeof role)}><option value="client">client</option><option value="server">server</option></select></label>
      <label>传输<select value={transport} onChange={event => setTransport(event.target.value as typeof transport)}><option value="internal">内部隔离（不发 SD）</option><option value="udp">UDP 在线</option><option value="tcp">TCP 在线</option></select></label>
      <label>部署实例<select value={instance || String(instances[0] ?? '')} onChange={event => setInstance(event.target.value)}>{instances.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
      <label>应用 ID<input value={applicationId} onChange={event => setApplicationId(event.target.value)} /></label>
      <label>明确标量字节序<select value={byteOrder} onChange={event => setByteOrder(event.target.value as typeof byteOrder)}><option value="big">大端</option><option value="little">小端</option></select></label>
      {transport === 'internal' && <label className="service-runtime-checkbox"><input type="checkbox" checked={includeInternalPeer} onChange={event => setIncludeInternalPeer(event.target.checked)} />同一会话加入内部测试对端</label>}
      {transport !== 'internal' && <><label>授权对端 IPv4<input value={peer} onChange={event => setPeer(event.target.value)} /></label><label>服务端口<input value={port} onChange={event => setPort(event.target.value)} /></label></>}
      <button className="button primary" disabled={busy || demo || !selected?.deploymentPath} onClick={() => void start()}><Icon name="play" />初始化所选服务</button>
    </div>
    {error && <div className="inline-notice error" role="alert">{error}</div>}
    {pollError && <div className="inline-notice error" role="alert">{pollError}</div>}
    {notice && <div className="inline-notice success" role="status">{notice}</div>}
    <div className="service-runtime-sessions">{sessions.map(item => <article key={item.id}>
      <button className={`button ghost${item.id === sessionId ? ' active' : ''}`} onClick={() => { setSessionId(item.id); setMemberKey(item.members[0]?.key ?? ''); setAction(item.members[0]?.role === 'server' ? 'notify' : 'call'); setFunctionName(''); setPending([]) }}>{item.application_name} · ID 0x{item.application_id.toString(16)} · {item.running ? `进程运行 PID ${item.pid}` : item.active ? '故障（需释放）' : '已停止'}</button>
      <small>模型 {item.model_id} · SHA-256 {item.source_sha256 ?? '未知'}</small>
      {item.members.map(value => <small key={value.key}>{value.key} · {value.transport} · {value.state} · socket {value.connected ? '已连接' : '已断开'}{value.last_error ? ` · ${value.last_error}` : ''}</small>)}
      {item.last_error && <span className="error">{item.last_error}</span>}
      {item.active && <button className="button secondary" disabled={busy} onClick={() => void run('停止原生服务会话', async () => { await api.stopServiceSession(item.id); setSessions(await api.serviceSessions()); setPending([]) })}><Icon name="stop" />停止并释放会话</button>}
    </article>)}</div>
    {session && <div className="service-runtime-commands">
      <div className="service-runtime-form">
        <label>成员<select value={memberKey} onChange={event => { setMemberKey(event.target.value); setAction(session.members.find(item => item.key === event.target.value)?.role === 'server' ? 'notify' : 'call'); setFunctionName('') }}>{session.members.map(item => <option key={item.key}>{item.key}</option>)}</select></label>
        <label>操作<select value={action} onChange={event => { setAction(event.target.value as typeof action); setFunctionName('') }}><option value="call" disabled={member?.role !== 'client'}>方法调用（client）</option><option value="notify" disabled={member?.role !== 'server'}>事件发送（server）</option><option value="respond" disabled={member?.role !== 'server'}>请求响应（server）</option></select></label>
        <label>原生接口<select value={effectiveFunction} onChange={event => setFunctionName(event.target.value)}>{functions.map(value => <option key={value}>{value}</option>)}</select></label>
        <label>超时（秒，上限 30）<input value={timeout} onChange={event => setTimeout(event.target.value)} /></label>
        {action === 'respond' && <label>待响应 request_id<input value={requestId} onChange={event => setRequestId(event.target.value)} /></label>}
      </div>
      <label>明确 JSON 参数（不会自动回显或猜测业务响应）<textarea rows={4} value={args} onChange={event => setArgs(event.target.value)} /></label>
      <div className="context-actions"><button className="button primary" disabled={busy || commandBusy || !session.running || !effectiveFunction || (action === 'respond' && !requestId)} onClick={() => void command()}><Icon name="send" />{commandBusy ? '等待原生回执…' : '执行所选操作'}</button><button className="button secondary" disabled={busy || requestsBusy || !session.running} onClick={() => void run('读取服务端请求', async () => { setPending(await api.serviceRequests(session.id)) }, `requests:${session.id}`)}>读取待响应请求</button></div>
      {pending.map(item => <div className="service-runtime-request" key={`${item.member}-${item.request_id}-${item.received_at}`}><code>{item.member} · {item.function} · request_id {item.request_id} · {JSON.stringify(item.args)}</code><button className="button ghost" disabled={!item.reply_allowed || busy} onClick={() => { setMemberKey(item.member); setFunctionName(item.function); setRequestId(String(item.request_id)); setAction('respond'); setArgs('{}') }}>{item.reply_allowed ? '选择此请求并填写响应' : '无响应方法'}</button></div>)}
    </div>}
    {!!results.length && <div className="service-runtime-results" aria-live="polite">{results.map(item => <div key={item.id}><code>{item.member} · {item.functionName}</code><p>{item.text}</p></div>)}</div>}
  </section>
}
