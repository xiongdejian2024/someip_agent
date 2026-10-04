import { useEffect, useRef, useState } from 'react'
import { api, describeApiError } from '../api/client'
import { logError, logInfo } from '../api/logger'
import type { ProjectControls } from '../workbench/projects'
import { emptyNetworkForm, formFromProfile, networkBindingResetAllowed, networkDraftDocument, networkWriteAllowed, networkWriteUnknown, profileFromForm,
  type ManagedEnvironment, type NetworkEnvironmentStatus, type NetworkForm, type NetworkPlan } from '../workbench/networkEnvironment'
import './NetworkEnvironmentPage.css'

type Confirmation = { label: string; detail: string; work: () => Promise<unknown>; form: NetworkForm; projectKey: string }

export function NetworkHostPanel({ status, busy, blocked, refresh, confirmRecord, resetBinding }: {
  status: NetworkEnvironmentStatus | null; busy: boolean; blocked: boolean; refresh: () => void
  confirmRecord: (record: ManagedEnvironment, action: 'bind' | 'remove') => void; resetBinding: () => void
}) {
  return <section className="panel project-manager" aria-label="后端实际网卡环境">
    <div className="project-section-heading"><h3>后端实际网卡环境</h3><button className="button secondary" disabled={busy} onClick={refresh}>刷新实际环境（只读）</button></div>
    {!status ? <p role="status">尚未读取后端环境；不显示虚构网卡。</p> : <>
      <p>{status.available ? 'Linux iproute2 可读取' : status.reason} · 主机写入{status.write_enabled ? '已授权' : '未授权'}</p>
      <p>父网卡白名单：{status.allowed_parents.join('、') || '未配置'}；原生地址：<code>{status.native_unicast}</code></p>
      <p>活动网络资源：{status.active_tasks ? '尚未释放' : '无'}；启停操作：{status.activity.lifecycle_operations}；配置保护：{status.activity.configuring ? '进行中' : '无'}</p>
      <p className="muted">需在 Linux 后端安装 iproute2，并由管理员配置 NETWORK_CONFIG_ENABLED 和 NETWORK_CONFIG_INTERFACES、授予 NET_ADMIN。页面不提供提权或发送授权。</p>
      <div className="network-table-wrap"><table className="network-table"><caption>实际接口与地址</caption><thead><tr><th>网卡／索引</th><th>MTU／状态</th><th>IPv4 地址</th></tr></thead><tbody>
        {status.interfaces.map(link => <tr key={link.ifindex}><td>{link.ifname}<small>#{link.ifindex} · {link.link_type}</small></td><td>{link.mtu}<small>{link.flags.includes('UP') ? 'UP' : 'DOWN'}</small></td><td>{link.addr_info?.filter(item => item.family === 'inet').map(item => `${item.local}/${item.prefixlen}`).join('、') || '无'}</td></tr>)}
        {!status.interfaces.length && <tr><td colSpan={3}>无可读取的接口。</td></tr>}
      </tbody></table></div>
      <details><summary>主路由表（不修改默认路由）</summary><ul>{status.routes.map((route, i) => <li key={i}><code>{route.dst ?? 'default'}</code> → {route.dev ?? '未指定接口'}{route.gateway ? `，经 ${route.gateway}` : ''}</li>)}</ul></details>
      <div className="project-section-heading network-heading"><h3>本工具管理记录</h3><button className="button secondary" disabled={busy || blocked || !networkBindingResetAllowed(status)} onClick={resetBinding}>解除地址绑定</button></div>
      <p className="muted">记录状态不代表即时所有权验证；绑定／清理时后端会再次检查。解除绑定回到 127.0.0.1，仍不启动任务。</p>
      <div className="network-records">{status.managed.map(record => <article key={record.id} className="project-preview"><h4>{record.profile.name}</h4>
        <p>{record.profile.interface} · {record.profile.ipv4} · {({ pending: '写入未完成', failed: '部分失败／需核对', applied: '已应用回执', removed: '已清理' })[record.status]} · {record.id.slice(0, 8)}</p>
        <div className="context-actions"><button className="button secondary" disabled={busy || blocked || !networkWriteAllowed(status, record.profile.parent) || record.status !== 'applied'} onClick={() => confirmRecord(record, 'bind')}>绑定未来原生任务地址</button>
          <button className="button secondary" disabled={busy || blocked || !networkWriteAllowed(status, record.profile.parent) || record.status === 'removed'} onClick={() => confirmRecord(record, 'remove')}>清理自有配置</button></div>
      </article>)}{!status.managed.length && <p className="muted">尚无管理记录；不会接管已有网卡。</p>}</div>
    </>}
  </section>
}

export function NetworkEnvironmentPage({ visible, ready, controls, projectKey = 'workspace' }: { visible: boolean; ready: boolean; controls: ProjectControls; projectKey?: string }) {
  const [form, setForm] = useState<NetworkForm>(emptyNetworkForm)
  const [formDirty, setFormDirty] = useState(false)
  const [previous, setPrevious] = useState<string | null>(null)
  const [formProject, setFormProject] = useState(projectKey)
  const [status, setStatus] = useState<NetworkEnvironmentStatus | null>(null)
  const [plan, setPlan] = useState<{ result: NetworkPlan; form: NetworkForm; projectKey: string } | null>(null)
  const [working, setWorking] = useState(false)
  const [notice, setNotice] = useState<{ error: boolean; text: string } | null>(null)
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null)
  const [unknown, setUnknown] = useState(false)
  const [reviewed, setReviewed] = useState(false)
  const lock = useRef(false)
  const readSequence = useRef(0)
  const documentRef = useRef(controls.document); documentRef.current = controls.document
  const formRef = useRef(form); formRef.current = form
  const busy = working || !ready
  const originChanged = (formDirty || previous !== null) && formProject !== projectKey
  const refresh = async () => {
    const sequence = ++readSequence.current
    try {
      const observed = await api.networkEnvironment()
      if (sequence !== readSequence.current) return
      setStatus(observed); logInfo('已读取后端实际网卡环境，未修改网络')
    } catch (error) {
      logError('网卡实际环境读取失败', error)
      if (sequence === readSequence.current) { setStatus(null); setNotice({ error: true, text: `实际环境读取失败：${describeApiError(error)}；不采用之前的环境授权。` }) }
    }
  }
  useEffect(() => () => { readSequence.current += 1 }, [])
  useEffect(() => { if (visible && ready) void refresh() }, [visible, ready])
  useEffect(() => {
    if (!formDirty && !unknown && !working) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [formDirty, unknown, working])
  const edit = (change: Partial<NetworkForm>) => {
    if (!formDirty && previous === null) setFormProject(projectKey)
    setForm(current => ({ ...current, ...change })); setFormDirty(true); setPlan(null); setConfirmation(null)
  }
  const readOperation = async (label: string, work: () => Promise<void>) => {
    if (lock.current || !ready) return
    lock.current = true; setWorking(true); setNotice(null); setConfirmation(null)
    try { await work() } catch (error) { logError(label, error); setNotice({ error: true, text: `${label}失败：${describeApiError(error)}` }) }
    finally { lock.current = false; setWorking(false) }
  }
  const saveDraft = () => void readOperation('载入网卡工程草案', async () => {
    if (originChanged) throw new Error('表单来自此前工程，请重新选择草案或明确用于当前工程')
    const requested = controls.document, snapshot = form
    const candidate = networkDraftDocument(requested, profileFromForm(snapshot), previous)
    const validated = await api.validateProject(candidate)
    if (documentRef.current !== requested || formRef.current !== snapshot) throw new Error('工程或表单已变化，请核对后重新载入')
    controls.update(current => current === requested ? { ...current, network_profiles: validated.network_profiles } : current)
    setPrevious(profileFromForm(snapshot).interface); setFormDirty(false)
    setNotice({ error: false, text: '已校验并载入工程草案。请到“工程保存与加载”保存到磁盘；未修改主机网络。' })
    logInfo('网卡配置载入工程草案，未持久化或应用系统')
  })
  const preflight = () => void readOperation('网卡预检', async () => {
    const snapshot = form, result = await api.planNetworkEnvironment(profileFromForm(snapshot))
    if (formRef.current !== snapshot) throw new Error('表单已变化，请重新预检')
    setPlan({ result, form: snapshot, projectKey }); setNotice({ error: false, text: '预检完成，未修改系统；应用仍需主机授权和明确确认。' })
    logInfo('网卡预检完成，未应用系统')
  })
  const confirm = (label: string, detail: string, work: () => Promise<unknown>) => setConfirmation({ label, detail, work, form, projectKey })
  const write = async (pending: Confirmation) => {
    if (lock.current || !ready || unknown || formRef.current !== pending.form || pending.projectKey !== projectKey) return
    lock.current = true; setWorking(true); setNotice(null); setConfirmation(null)
    readSequence.current += 1; setStatus(null)
    try {
      await pending.work(); setPlan(null)
      setNotice({ error: false, text: `${pending.label}已收到完成回执；未启动 SOME/IP 任务，发送授权保持不变。` })
      logInfo('网卡环境明确操作完成', { action: pending.label })
    } catch (error) {
      logError('网卡环境明确操作失败', error, { action: pending.label })
      const uncertain = networkWriteUnknown(error)
      if (uncertain) { setUnknown(true); setReviewed(false) }
      setPlan(null); setNotice({ error: true, text: uncertain ? `${pending.label}结果未确认或可能部分写入：${describeApiError(error)}。已锁定进一步写入，请刷新并由管理员核对，不要重复应用。` : `${pending.label}被拒绝：${describeApiError(error)}；请重新预检或核对环境。` })
    } finally { lock.current = false; setWorking(false) }
    await refresh()
    // 读取失败与写入回执分别报告；不把读取成功当成未知写入已被核实。
  }
  const confirmRecord = (record: ManagedEnvironment, action: 'bind' | 'remove') => confirm(action === 'bind' ? '绑定原生地址' : '清理自有配置',
    `${record.profile.interface} · ${record.profile.ipv4} · 管理记录 ${record.id}。${action === 'remove' ? '仅清理本工具可验证的资源；其他程序改动会阻止清理。已绑定时须先解除。' : '只影响下一次原生任务启动，不允许自动发送。'}`,
    () => action === 'bind' ? api.bindNetworkEnvironment(record.id) : api.removeNetworkEnvironment(record.id))
  const profiles = controls.document.network_profiles ?? []
  const currentPlan = plan?.form === form && plan.projectKey === projectKey ? plan.result : null
  return <div className="network-environment-page" hidden={!visible} aria-label="VLAN 与网卡环境配置">
    <p className="network-boundary">配置作用于后端主机，不是浏览器电脑。工程保存／加载不会改网卡；系统应用也不会启动服务或仿真。</p>
    {notice && <p className={`project-notice${notice.error ? ' error' : ''}`} role={notice.error ? 'alert' : 'status'}>{notice.text}</p>}
    {unknown && <section className="project-confirmation" aria-label="未知网卡写入保护"><strong>进一步写入已锁定</strong><p>先刷新实际接口、地址、路由和管理记录，再由管理员核对系统日志。读取成功本身不证明写入成功或失败。</p>
      <label className="network-check"><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} disabled={busy} />已由管理员核对未知结果与自有资源，不重复提交原请求</label>
      <button className="button secondary" disabled={busy || !reviewed || !status} onClick={() => { setUnknown(false); setReviewed(false); setPlan(null); setConfirmation(null); logInfo('用户明确确认网卡未知结果已核对，解除本页写入锁') }}>确认核对并解除本页锁</button>
    </section>}
    {confirmation && <section className="project-confirmation" aria-label="确认主机网卡操作"><strong>{confirmation.label}</strong><p>{confirmation.detail}</p><p>操作会改变后端主机环境，不影响发送授权；不会自动运行任务。</p><div className="context-actions">
      <button className="button secondary" disabled={busy} onClick={() => setConfirmation(null)}>返回，不操作</button><button className="button primary" disabled={busy || unknown || form !== confirmation.form || confirmation.projectKey !== projectKey} onClick={() => void write(confirmation)}>明确确认主机操作</button>
    </div></section>}
    <div className="network-columns"><section className="panel project-manager" aria-label="网卡工程草案">
      <h3>网卡工程草案</h3><p>先填写或选择配置，再载入工程草案。保存到磁盘在独立的“工程保存与加载”页完成。</p>
      <div className="network-records">{profiles.map(item => <div key={item.interface} className="network-draft-row"><button className="project-list-item" disabled={busy} onClick={() => {
        if (formDirty && !window.confirm('表单尚未载入工程，替换会丢弃表单更改；是否继续？')) return
        setForm(formFromProfile(item)); setPrevious(item.interface); setFormProject(projectKey); setFormDirty(false); setPlan(null); setConfirmation(null)
      }}><strong>{item.name}</strong><span>{item.interface} · {item.ipv4} · {item.vlan_id === null ? '无 VLAN' : `VLAN ${item.vlan_id}`}</span></button>
        <button className="button secondary" disabled={busy} onClick={() => { controls.update(doc => ({ ...doc, network_profiles: (doc.network_profiles ?? []).filter(profile => profile.interface !== item.interface) })); setPlan(null); setConfirmation(null); logInfo('仅从工程草案移除网卡配置，未清理系统') }}>从草案移除</button></div>)}{!profiles.length && <p className="muted">尚无网卡草案。此列表不是系统已应用网卡。</p>}</div>
      <button className="button secondary" disabled={busy} onClick={() => {
        if (formDirty && !window.confirm('清空会丢弃尚未载入工程的表单更改；是否继续？')) return
        setForm(emptyNetworkForm()); setPrevious(null); setFormProject(projectKey); setFormDirty(false); setPlan(null); setConfirmation(null)
      }}>填写新配置</button>
      <p className="muted">{formDirty ? '表单有尚未载入工程的更改；它不会随工程保存，切换工程不会自动清空表单。' : previous ? '正在编辑已选配置；系统状态请看右侧。' : '表单为空，未创建工程或系统配置。'}</p>
      {originChanged && <section className="project-confirmation" aria-label="核对跨工程网卡表单"><strong>工作区工程已切换，原表单保留但已禁止载入或应用</strong><p>请选择当前工程的草案，或明确将保留的表单作为当前工程的新配置。</p>
        <button className="button secondary" disabled={busy} onClick={() => { setFormProject(projectKey); setPrevious(null); setFormDirty(true); setPlan(null); setConfirmation(null); logInfo('用户明确将保留网卡表单用于当前工程，未应用系统') }}>将保留表单用于当前工程</button>
      </section>}
      <form onSubmit={event => { event.preventDefault(); saveDraft() }}><fieldset disabled={busy} className="network-form">
        <label className="project-field">环境名称<input required maxLength={128} aria-label="网络环境名称" value={form.name} onChange={event => edit({ name: event.target.value })} /></label>
        <label className="project-field">模式<select aria-label="网卡配置模式" value={form.mode} onChange={event => edit({ mode: event.target.value as NetworkForm['mode'] })}><option value="vlan">新建 802.1Q VLAN</option><option value="untagged">已有网卡增加自有 IPv4</option></select></label>
        <label className="project-field">父网卡<input required maxLength={15} list="network-parent-options" aria-label="父网卡名称" value={form.parent} onChange={event => edit({ parent: event.target.value })} placeholder="后端专用测试网卡名称" /></label>
        <datalist id="network-parent-options">{status?.interfaces.filter(item => item.link_type === 'ether').map(item => <option key={item.ifindex} value={item.ifname} />)}</datalist>
        {form.mode === 'vlan' && <><label className="project-field">VLAN 子网卡名称<input required maxLength={15} aria-label="VLAN 子网卡名称" value={form.interface} onChange={event => edit({ interface: event.target.value })} /></label>
          <label className="project-field">VLAN ID<input required type="number" min={1} max={4094} step={1} aria-label="VLAN ID" value={form.vlan} onChange={event => edit({ vlan: event.target.value })} /></label>
          <label className="project-field">MTU（可选，不能超过父网卡）<input type="number" min={576} max={9000} step={1} aria-label="VLAN MTU" value={form.mtu} onChange={event => edit({ mtu: event.target.value })} /></label></>}
        <label className="project-field">IPv4 / 前缀<input required aria-label="网卡 IPv4 CIDR" value={form.ipv4} onChange={event => edit({ ipv4: event.target.value })} placeholder="例：192.168.10.2/24" /></label>
        <label className="project-field">SD 组播路由目标（可选）<input aria-label="SD 组播地址" value={form.multicast} onChange={event => edit({ multicast: event.target.value })} placeholder="IPv4 组播地址，不修改发送配置" /></label>
        <div className="context-actions network-form-actions"><button className="button primary" type="submit" disabled={originChanged}>校验并载入工程草案（不应用）</button><button className="button secondary" type="button" disabled={!status?.available || originChanged} onClick={preflight}>预检系统应用（只读）</button></div>
      </fieldset></form>
      {currentPlan && <section className="project-preview network-plan" aria-label="网卡应用计划"><h4>待确认的系统命令计划</h4><pre>{currentPlan.commands.map(command => `ip ${command.join(' ')}`).join('\n')}</pre><p>不替换默认路由，不接管既有接口；令牌只对当前环境有效。</p>
        <button className="button primary" disabled={busy || unknown || !networkWriteAllowed(status, currentPlan.profile.parent)} onClick={() => confirm('应用网卡环境', `${currentPlan.profile.interface} · ${currentPlan.profile.ipv4}；将执行以上已预检命令。`, () => api.applyNetworkEnvironment(currentPlan.profile, currentPlan.token))}>申请应用并确认</button>
      </section>}
    </section><NetworkHostPanel status={status} busy={busy} blocked={unknown} refresh={() => { setPlan(null); setConfirmation(null); void readOperation('刷新实际网卡环境', refresh) }} confirmRecord={confirmRecord}
      resetBinding={() => confirm('解除原生地址绑定', '原生地址将回到 127.0.0.1；须先停止并释放全部网络任务。', () => api.bindNetworkEnvironment(null))} /></div>
  </div>
}
