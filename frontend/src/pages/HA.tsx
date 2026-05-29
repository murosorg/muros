import { useEffect, useRef, useState } from 'react'
import { api, type HaConfig, type HaVip, type HaVipInput, type HaStatus } from '../lib/api'
import PageHeader from '../components/PageHeader'
import { Cable } from 'lucide-react'
import EmptyState from '../components/EmptyState'
import HelpTooltip from '../components/HelpTooltip'
import Toggle from '../components/Toggle'
import FormActions from '../components/FormActions'
import { isDirty } from '../lib/dirty'
import CardHeader from '../components/CardHeader'
import CidrInput from '../components/CidrInput'
import ConfirmModal, { useConfirm } from '../components/ConfirmModal'
import { ErrorBlock, SuccessBlock } from '../components/Alerts'
import { ServiceStatusInline, type ServiceState } from '../components/ServiceStatusLine'
import { fmt } from '../lib/format'

const MISSING_PKGS_HINT =
  "Installation is done via apt on this node. Remember to repeat the same operation on the second firewall."

const BLANK_VIP: HaVipInput = {
  vrid: 50,
  interface: '',
  vip_cidr: '',
  auth_pass: 'muros',
  priority: null,
  description: '',
  enabled: true,
}

export default function HA() {
  const [vips, setVips] = useState<HaVip[]>([])
  const [status, setStatus] = useState<HaStatus | null>(null)
  const [draft, setDraft] = useState<HaConfig | null>(null)
  // Last server snapshot, used to detect unsaved edits and drive the
  // orange Apply dot.
  const [loadedCfg, setLoadedCfg] = useState<HaConfig | null>(null)
  // The sub-panel HaSyncPanel reports its own dirty state up so the
  // master Apply dot lights even when only the sync form changed.
  const [syncDirty, setSyncDirty] = useState(false)
  const [editing, setEditing] = useState<HaVipInput | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  // Distinct from `busy`: only flips while the operator clicks the
  // service on/off toggle, so the small spinner next to the toggle in
  // the PageHeader does not also fire during a regular Apply.
  const [toggleBusy, setToggleBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { confirm: confirmFn, ConfirmHost } = useConfirm()
  // HaSyncPanel registers its own save handler here so the master Apply
  // button in the page header can flush both keepalived config and the
  // ha-sync (peer URL/token) config in a single click.
  const syncSaveRef = useRef<(() => Promise<void>) | null>(null)

  const applyAll = async () => {
    await saveConfig()
    if (syncSaveRef.current) {
      try { await syncSaveRef.current() } catch { /* surfaced inside the sync panel */ }
    }
  }

  const reload = async () => {
    try {
      const [c, v, s] = await Promise.all([
        api.ha.getConfig(),
        api.ha.listVips(),
        api.ha.status(),
      ])
      setDraft(c)
      setLoadedCfg(c)
      setVips(v)
      setStatus(s)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  useEffect(() => {
    reload()
    const id = setInterval(() => {
      api.ha.status().then(setStatus).catch(() => {})
    }, 5000)
    return () => clearInterval(id)
  }, [])

  const saveConfig = async () => {
    if (!draft) return
    setBusy(true); setError(null); setMessage(null)
    try {
      const c = await api.ha.setConfig(draft)
      setDraft(c); setLoadedCfg(c)
      const r = await api.ha.apply()
      setMessage(`${r.dry_run ? 'DRY-RUN : ' : ''}${r.message}`)
      await reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // Header dot toggle : flips the persisted HaConfig.enabled flag and
  // applies immediately. keepalived and conntrackd are started / stopped
  // together since they form a single HA service from the operator
  // point of view. Off then on acts as a restart of the failover stack.
  const toggleService = async () => {
    if (!loadedCfg) return
    const next = !loadedCfg.enabled
    const ok = await confirmFn(next ? {
      title: 'Enable high availability ?',
      message: 'keepalived and conntrackd will be started now and at every boot. If this firewall is configured as MASTER, the VIPs will move here. Make sure the peer is reachable on the sync interface before enabling HA on a production network.',
      confirmLabel: 'Enable',
    } : {
      title: 'Disable high availability ?',
      message: 'keepalived and conntrackd will be stopped immediately. All VIPs will be removed from this firewall, traffic that arrives on a VIP address will stop being served until the peer takes over or HA is re-enabled. Active connections currently tracked on this node will not be synced to the peer.',
      confirmLabel: 'Disable',
      destructive: true,
    })
    if (!ok) return
    setToggleBusy(true); setError(null); setMessage(null)
    try {
      const c = await api.ha.setConfig({ ...loadedCfg, enabled: next })
      setDraft(c); setLoadedCfg(c)
      const r = await api.ha.apply()
      setMessage(`${r.dry_run ? 'DRY-RUN : ' : ''}${r.message}`)
      await reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setToggleBusy(false)
    }
  }

  const saveVip = async () => {
    if (!editing) return
    setBusy(true); setError(null); setMessage(null)
    try {
      if (editingId === null) {
        await api.ha.createVip(editing)
      } else {
        await api.ha.updateVip(editingId, editing)
      }
      setEditing(null); setEditingId(null)
      const r = await api.ha.apply()
      setMessage(`${r.dry_run ? 'DRY-RUN : ' : ''}${r.message}`)
      await reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const removeVip = async (id: number) => {
    const v = vips.find((x) => x.id === id)
    const ok = await confirmFn({
      title: 'Delete this VIP',
      message: v ? <p>VIP <span className="font-mono">{v.vip_cidr}</span> on <span className="font-mono">{v.interface}</span> (VRID {v.vrid}) will be removed from keepalived.</p> : 'This VIP will be removed.',
      destructive: true,
      requireText: 'delete',
    })
    if (!ok) return
    setBusy(true); setError(null); setMessage(null)
    try {
      await api.ha.deleteVip(id)
      const r = await api.ha.apply()
      setMessage(`${r.dry_run ? 'DRY-RUN : ' : ''}${r.message}`)
      await reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const installPackages = async () => {
    setBusy(true); setError(null); setMessage(null)
    try {
      const r = await api.ha.install()
      if (r.newly_installed.length > 0) {
        setMessage(`Installed packages : ${r.newly_installed.join(', ')}. ${MISSING_PKGS_HINT}`)
      } else if (r.already_present.length > 0 && r.installed) {
        setMessage('Packages already installed, nothing to do.')
      } else {
        setMessage(r.output_tail || 'Installation simulated (dry-run mode).')
      }
      await reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <PageHeader
        icon={<Cable size={16} />}
        title="High availability"
        description="Active/passive failover with live session sync."
        status={status && (
          <span className="inline-flex items-center gap-4 flex-wrap">
            <ServiceStatusInline
              name="keepalived"
              state={(status.keepalived_state ?? (status.keepalived_active ? 'active' : 'inactive')) as ServiceState}
              version={status.keepalived_version}
            />
            <ServiceStatusInline
              name="conntrackd"
              state={(status.conntrackd_state ?? (status.conntrackd_active ? 'active' : 'inactive')) as ServiceState}
              version={status.conntrackd_version}
            />
          </span>
        )}
        serviceEnabled={!!loadedCfg?.enabled}
        serviceToggleBusy={toggleBusy || !loadedCfg}
        serviceToggleTitle={loadedCfg?.enabled
          ? 'High availability enabled. Click to stop keepalived + conntrackd and disable them at boot.'
          : 'High availability disabled. Click to start keepalived + conntrackd and enable them at boot.'}
        onServiceEnabledChange={toggleService}
        actions={draft && (
          <FormActions
            onApply={applyAll}
            busy={busy}
            dirty={isDirty(draft, loadedCfg) || syncDirty}
          />
        )}
      />

      <div className="px-6 py-4 space-y-6">
        {error && <ErrorBlock message={error} />}
        {message && <SuccessBlock message={message} onDismiss={() => setMessage(null)} />}

        {/* Etat live */}
        <StatusPanel status={status} onInstall={installPackages} busy={busy} />

        {/* Config */}
        {draft && (
          <ConfigPanel
            draft={draft}
            onChange={setDraft}
          />
        )}

        {/* VIPs */}
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">Virtual IPs (VIPs)</h2>
            <button className="btn-primary" onClick={() => { setEditing(BLANK_VIP); setEditingId(null) }}>
              Add a VIP
            </button>
          </div>
          <VipTable
            vips={vips}
            onEdit={(v) => { setEditing({ ...v }); setEditingId(v.id) }}
            onDelete={removeVip}
            onAdd={() => { setEditing(BLANK_VIP); setEditingId(null) }}
          />
          {editing && (
            <VipForm
              value={editing}
              onChange={setEditing}
              onSave={saveVip}
              onCancel={() => { setEditing(null); setEditingId(null) }}
              busy={busy}
            />
          )}
        </div>

        <HaSyncPanel
          registerSave={(fn) => { syncSaveRef.current = fn }}
          onDirtyChange={setSyncDirty}
        />
      </div>
      <ConfirmHost />
    </div>
  )
}


function StatusPanel({ status, onInstall, busy }: {
  status: HaStatus | null
  onInstall: () => void
  busy: boolean
}) {
  if (!status) return null
  const installed = status.keepalived_installed && status.conntrackd_installed
  const missing: string[] = []
  if (!status.keepalived_installed) missing.push('keepalived')
  if (!status.conntrackd_installed) missing.push('conntrackd')
  return (
    <>
      {!installed && (
        <div className="bg-amber-50 border border-amber-200 text-amber-900 px-3 py-3 rounded text-sm">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div>
              <div className="font-medium">Missing packages on this node</div>
              <div className="mt-1">
                To install: <code>{missing.join(', ')}</code>. {MISSING_PKGS_HINT}
              </div>
            </div>
            <button
              className="btn-primary whitespace-nowrap"
              onClick={onInstall}
              disabled={busy}
            >
              {busy ? 'Installing...' : 'Install now'}
            </button>
          </div>
        </div>
      )}
      {status.vrrp_instances.length > 0 && (
        <div className="card">
          <div className="text-sm font-medium mb-2">VRRP state per instance</div>
          <div className="flex flex-wrap gap-2">
            {status.vrrp_instances.map((vi) => {
              const live = vi.state === 'MASTER' || vi.state === 'BACKUP'
              const dotColor = vi.state === 'MASTER' ? 'bg-emerald-500'
                : vi.state === 'BACKUP' ? 'bg-amber-500'
                : 'bg-red-500'
              return (
                <span key={vi.name} className={`text-xs px-2 py-1 rounded font-mono border inline-flex items-center gap-1.5 ${
                  vi.state === 'MASTER' ? 'bg-emerald-50 border-emerald-300 text-emerald-800' :
                  vi.state === 'BACKUP' ? 'bg-amber-50 border-amber-300 text-amber-800' :
                  'bg-red-50 border-red-300 text-red-800'
                }`}>
                  <span className="relative inline-flex w-2 h-2">
                    {live && (
                      <span className={`absolute inset-0 rounded-full ${dotColor} opacity-60 animate-ping`} />
                    )}
                    <span className={`relative inline-flex rounded-full w-2 h-2 ${dotColor}`} />
                  </span>
                  {vi.name} : {vi.state}
                </span>
              )
            })}
          </div>
          <div className="text-[11px] text-gray-500 mt-1">
            Pulsing dot = VRRP advertisements seen on the sync interface (peer reachable).
          </div>
        </div>
      )}
      {Object.keys(status.conntrack_stats).length > 0 && (
        <div className="card">
          <div className="text-sm font-medium mb-2">Sync conntrackd</div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
            {Object.entries(status.conntrack_stats).map(([k, v]) => (
              <div key={k} className="bg-slate-50 border border-slate-200 rounded px-3 py-2">
                <div className="text-xs text-gray-600">{k}</div>
                <div className="font-mono">{v}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  )
}

function ConfigPanel({ draft, onChange }: {
  draft: HaConfig
  onChange: (c: HaConfig) => void
}) {
  return (
    <div className="card">
      <h2 className="text-lg font-semibold mb-3">Configuration</h2>

      {/* Enable/disable lives on the page header toggle (with a
          confirmation modal). Removed here to avoid two switches. */}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="block text-sm text-gray-700 mb-1">Role of this node</label>
          <select className="input" value={draft.role} onChange={(e) => onChange({ ...draft, role: e.target.value as 'primary' | 'secondary' })}>
            <option value="primary">Primary (priority 150)</option>
            <option value="secondary">Secondary (priority 100)</option>
          </select>
        </div>

        <div>
          <label className="block text-sm text-gray-700 mb-1">Peer IP address</label>
          <input className="input" value={draft.peer_address} onChange={(e) => onChange({ ...draft, peer_address: e.target.value })} placeholder="10.0.0.2" />
          <p className="text-xs text-gray-500 mt-1">IP of the other firewall on the sync link.</p>
        </div>

        <div>
          <label className="block text-sm text-gray-700 mb-1">Sync interface</label>
          <input className="input" value={draft.sync_interface} onChange={(e) => onChange({ ...draft, sync_interface: e.target.value })} placeholder="eth2" />
          <p className="text-xs text-gray-500 mt-1">Dedicated cross-link recommended (port to port between the two firewalls).</p>
        </div>

        <div className="flex items-center gap-2 md:pt-1">
          <Toggle checked={draft.preempt} onChange={(v) => onChange({ ...draft, preempt: v })} />
          <span className="text-sm">Preempt (primary takes back the lead as soon as it returns)</span>
          <HelpTooltip text="If OFF (production recommended): when the master fails the backup takes the VIP and keeps it even after the master returns, avoiding flap. If ON: the master automatically takes back control upon return (useful in lab or if a node is significantly more powerful)." />
        </div>
      </div>

    </div>
  )
}

function VipTable({ vips, onEdit, onDelete, onAdd }: {
  vips: HaVip[]
  onEdit: (v: HaVip) => void
  onDelete: (id: number) => void
  onAdd?: () => void
}) {
  if (vips.length === 0) {
    return <EmptyState
      text="No VIP"
      hint="Add a VIP to enable high availability via keepalived (VRRP)."
      action={onAdd && <button className="btn-primary" onClick={onAdd}>Add a VIP</button>}
    />
  }
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-gray-600 border-b">
        <tr>
          <th className="py-2">VRID</th>
          <th>Interface</th>
          <th>VIP</th>
          <th>Priorite</th>
          <th>Description</th>
          <th>Enabled</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {vips.map((v) => (
          <tr key={v.id} className="border-b last:border-0">
            <td className="py-2 font-mono">{v.vrid}</td>
            <td className="font-mono">{v.interface}</td>
            <td className="font-mono">{v.vip_cidr}</td>
            <td>{v.priority ?? '(auto)'}</td>
            <td>{v.description || ''}</td>
            <td>{v.enabled ? 'oui' : 'non'}</td>
            <td className="text-right">
              <button className="btn-ghost py-1" onClick={() => onEdit(v)}>Edit</button>
              <button className="btn-ghost py-1 text-red-700 hover:text-red-800" onClick={() => onDelete(v.id)}>Delete</button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function VipForm({ value, onChange, onSave, onCancel, busy }: {
  value: HaVipInput
  onChange: (v: HaVipInput) => void
  onSave: () => void
  onCancel: () => void
  busy: boolean
}) {
  return (
    <div className="mt-4 p-4 bg-slate-50 rounded border border-slate-200">
      <div className="flex items-center justify-between gap-3 mb-3">
        <h3 className="font-medium">VIP</h3>
        <FormActions
          onApply={onSave}
          busy={busy}
          extra={
            <button className="btn-secondary" onClick={onCancel} disabled={busy}>
              Cancel
            </button>
          }
        />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <label className="block text-sm text-gray-700 mb-1">VRID (1-255)</label>
          <input className="input" type="number" min={1} max={255} value={value.vrid}
            onChange={(e) => onChange({ ...value, vrid: parseInt(e.target.value || '0', 10) })} />
        </div>
        <div>
          <label className="block text-sm text-gray-700 mb-1">Interface</label>
          <input className="input" value={value.interface} placeholder="eth0, lan, ..." onChange={(e) => onChange({ ...value, interface: e.target.value })} />
        </div>
        <div>
          <label className="block text-sm text-gray-700 mb-1">VIP (address + mask)</label>
          <CidrInput
            value={value.vip_cidr}
            onChange={(v) => onChange({ ...value, vip_cidr: v })}
            placeholder="192.0.2.10"
          />
        </div>
        <div>
          <label className="block text-sm text-gray-700 mb-1">
            VRRP password (8 chars max)
            <HelpTooltip text="Shared secret between both VRRP nodes to validate announcements. Same on master + backup. Max 8 characters (VRRPv2 protocol limit)." />
          </label>
          <input className="input" value={value.auth_pass} maxLength={8} onChange={(e) => onChange({ ...value, auth_pass: e.target.value })} />
        </div>
        <div>
          <label className="block text-sm text-gray-700 mb-1">
            Priority (override)
            <HelpTooltip text="VRRP priority for this VIP. The node with the highest priority becomes MASTER. Empty = computed automatically (110 if master, 100 if backup). Useful range: 1-254." />
          </label>
          <input className="input" type="number" placeholder="leave empty for automatic" value={value.priority ?? ''}
            onChange={(e) => onChange({ ...value, priority: e.target.value ? parseInt(e.target.value, 10) : null })} />
        </div>
        <div className="md:col-span-2">
          <label className="block text-sm text-gray-700 mb-1">Description</label>
          <input className="input" value={value.description ?? ''} onChange={(e) => onChange({ ...value, description: e.target.value })} />
        </div>
        <div className="flex items-center gap-2 md:col-span-2">
          <Toggle checked={value.enabled} onChange={(v) => onChange({ ...value, enabled: v })} />
          <span className="text-sm">VIP active</span>
        </div>
      </div>
    </div>
  )
}

function HaSyncPanel({ registerSave, onDirtyChange }: {
  registerSave: (fn: () => Promise<void>) => void
  onDirtyChange?: (dirty: boolean) => void
}) {
  const [cfg, setCfg] = useState<import('../lib/api').HaSyncConfig | null>(null)
  const [form, setForm] = useState<import('../lib/api').HaSyncConfigInput | null>(null)
  const [logs, setLogs] = useState<import('../lib/api').HaSyncLog[]>([])
  const [role, setRole] = useState<import('../lib/api').HaSyncRole | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  const reload = async () => {
    try {
      const [c, l, r] = await Promise.all([
        api.haSync.getConfig(),
        api.haSync.getLog(),
        api.haSync.getRole(),
      ])
      setCfg(c); setLogs(l); setRole(r)
      if (!form) {
        setForm({
          enabled: c.enabled, peer_url: c.peer_url, peer_token: c.peer_token,
          sync_mode: c.sync_mode, verify_tls: c.verify_tls,
        })
      }
    } catch (e) { setErr((e as Error).message) }
  }
  useEffect(() => { reload() }, [])

  const save = async () => {
    if (!form) return
    setBusy(true); setErr(null); setMsg(null)
    try {
      const c = await api.haSync.updateConfig(form)
      setCfg(c)
      setMsg('Configuration saved.')
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  // Expose save() to the page-level master Apply so the top-right
  // button flushes keepalived config AND ha-sync config in one click.
  useEffect(() => { registerSave(save) }, [form])

  // Report dirty state up so the master Apply button can light its dot
  // even when only the sync sub-panel has unsaved edits.
  const syncDirty = isDirty(form, cfg && {
    enabled: cfg.enabled, peer_url: cfg.peer_url, peer_token: cfg.peer_token,
    sync_mode: cfg.sync_mode, verify_tls: cfg.verify_tls,
  })
  useEffect(() => { onDirtyChange?.(syncDirty) }, [syncDirty])

  const genToken = async () => {
    if (!form) return
    if (form.peer_token && !confirm('Replace the existing token? The peer will need to receive the new token.')) return
    try {
      const r = await api.haSync.generateToken()
      setForm({ ...form, peer_token: r.token })
    } catch (e) { setErr((e as Error).message) }
  }

  const test = async () => {
    setBusy(true); setErr(null); setMsg(null)
    try {
      const r = await api.haSync.test()
      if (r.success) setMsg(`Peer reachable. Remote role: ${r.peer_role || 'unknown'}. Version: ${r.peer_version || 'unknown'}.`)
      else setErr(`Test failed : ${r.error || 'unknown error'}`)
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  const [pushPreview, setPushPreview] = useState<{
    peer_role: string | null
    peer_version: string | null
    last_push: { ts: string; ok: boolean; size: number } | null
    reachable: boolean
    peer_error: string | null
  } | null>(null)

  const openPushPreview = async () => {
    setBusy(true); setErr(null); setMsg(null)
    try {
      let peer_role: string | null = null
      let peer_version: string | null = null
      let reachable = false
      let peer_error: string | null = null
      try {
        const t = await api.haSync.test()
        if (t.success) {
          reachable = true
          peer_role = t.peer_role || null
          peer_version = t.peer_version || null
        } else {
          peer_error = t.error || 'unknown error'
        }
      } catch (e) {
        peer_error = (e as Error).message
      }
      const lastPush = logs.find((l) => l.direction === 'push' && l.success)
      setPushPreview({
        peer_role, peer_version,
        last_push: lastPush ? {
          ts: lastPush.created_at,
          ok: lastPush.success,
          size: lastPush.db_size_bytes,
        } : null,
        reachable, peer_error,
      })
    } finally { setBusy(false) }
  }

  const doPush = async () => {
    setErr(null); setMsg(null)
    try {
      const r = await api.haSync.push()
      setMsg(`Push succeeded in ${r.duration_ms} ms (${(r.db_size_bytes / 1024).toFixed(1)} KB transferred).`)
      setPushPreview(null)
      await reload()
    } catch (e) {
      setErr((e as Error).message)
      setPushPreview(null)
      await reload()
    }
  }

  if (!form) return null

  return (
    <div className="card">
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            Configuration synchronization
            {role && (
              <span className={`text-[11px] px-2 py-0.5 rounded font-mono font-medium normal-case tracking-normal ${
                role.role === 'MASTER' ? 'bg-emerald-100 text-emerald-800' :
                role.role === 'BACKUP' ? 'bg-amber-100 text-amber-800' :
                role.role === 'FAULT' ? 'bg-red-100 text-red-800' :
                'bg-slate-100 text-slate-700'
              }`}>{role.role.toLowerCase()}</span>
            )}
          </span>
        }
      >
        <div className="flex items-center gap-2">
          <button className="btn-secondary" onClick={test} disabled={busy || !cfg?.enabled}>
            Test the connection
          </button>
          <button className="btn-secondary" onClick={openPushPreview} disabled={busy || !cfg?.enabled || !role?.writable}>
            Push now...
          </button>
        </div>
      </CardHeader>

      <div className="text-xs text-gray-600 mb-3">
        The MASTER node pushes its SQLite DB to the BACKUP at each apply (auto mode)
        or on manual action. The BACKUP refuses UI changes until it has
        become MASTER. The token is shared between the 2 nodes.
      </div>

      {err && <div className="mb-3"><ErrorBlock message={err} /></div>}
      {msg && <SuccessBlock message={msg} onDismiss={() => setMsg(null)} />}

      <div className="flex items-center gap-2 mb-3">
        <Toggle checked={form.enabled}
          onChange={(v) => setForm({ ...form, enabled: v })} />
        <span className="text-sm font-medium">HA synchronization enabled</span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="block">
          <div className="text-sm font-medium mb-1">Peer URL</div>
          <input className="input" value={form.peer_url}
            placeholder="https://muros-backup.local"
            onChange={(e) => setForm({ ...form, peer_url: e.target.value })} />
          <div className="text-xs text-gray-600 mt-1">HTTPS URL of the second firewall</div>
        </label>
        <label className="block">
          <div className="text-sm font-medium mb-1">Mode</div>
          <select className="input" value={form.sync_mode}
            onChange={(e) => setForm({ ...form, sync_mode: e.target.value })}>
            <option value="auto">auto (push after each apply)</option>
            <option value="manual">manual (button only)</option>
          </select>
        </label>
      </div>

      <div className="mt-3 space-y-1">
        <div className="flex items-center justify-between">
          <div className="text-sm font-medium">Sync token</div>
          <button className="btn-secondary text-xs" type="button" onClick={genToken}>Generate a token</button>
        </div>
        <input className="input font-mono text-xs" value={form.peer_token}
          onChange={(e) => setForm({ ...form, peer_token: e.target.value })}
          placeholder="shared secret between the 2 nodes (64 hex chars)" />
        <div className="text-xs text-gray-600">
          Enter the SAME token on both nodes. Once generated, copy into the peer config.
        </div>
      </div>

      <div className="flex items-center gap-2 mt-3">
        <Toggle checked={form.verify_tls}
          onChange={(v) => setForm({ ...form, verify_tls: v })} />
        <span className="text-sm">Verify peer TLS certificate</span>
        <span className="text-xs text-gray-600">(disable if self-signed cert)</span>
      </div>

      <div className="mt-4">
        <div className="text-sm font-medium mb-2">Sync history</div>
        {logs.length === 0 ? (
          <EmptyState text="No sync yet" variant="inline" />
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-gray-600 border-b">
              <tr>
                <th className="py-2">Date</th>
                <th>Direction</th>
                <th>Declencheur</th>
                <th>Taille</th>
                <th>Duration</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((l) => (
                <tr key={l.id} className="border-b last:border-0">
                  <td className="py-2 font-mono text-xs whitespace-nowrap">
                    {fmt.datetime(l.created_at)}
                  </td>
                  <td className="font-mono text-xs">{l.direction}</td>
                  <td className="font-mono text-xs">{l.triggered_by}</td>
                  <td className="font-mono text-xs">{(l.db_size_bytes / 1024).toFixed(1)} Ko</td>
                  <td className="font-mono text-xs">{l.duration_ms} ms</td>
                  <td>
                    <span className={`text-xs px-2 py-1 rounded font-medium ${
                      l.success ? 'bg-emerald-100 text-emerald-800' : 'bg-red-100 text-red-800'
                    }`} title={l.error || ''}>
                      {l.success ? 'OK' : 'failed'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <ConfirmModal
        open={!!pushPreview}
        title="Push configuration to peer"
        destructive
        confirmLabel="Push now"
        requireText="push"
        onConfirm={doPush}
        onCancel={() => setPushPreview(null)}
        message={
          pushPreview ? (
            <div className="space-y-3 text-sm">
              <div className="bg-amber-50 border border-amber-200 text-amber-900 rounded px-3 py-2">
                The peer's SQLite DB will be <strong>fully overwritten</strong> by this node's current state.
                There is no diff: the entire DB is transferred. Any pending change on the peer is lost.
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="bg-slate-50 border border-slate-200 rounded px-3 py-2">
                  <div className="text-xs font-semibold text-gray-600 mb-1">This node (source)</div>
                  <div className="text-xs">Role: <span className="font-mono">{role?.role || 'unknown'}</span></div>
                  <div className="text-xs">Writable: <span className="font-mono">{String(role?.writable ?? false)}</span></div>
                </div>
                <div className={`border rounded px-3 py-2 ${pushPreview.reachable ? 'bg-slate-50 border-slate-200' : 'bg-red-50 border-red-200'}`}>
                  <div className="text-xs font-semibold text-gray-600 mb-1">Peer (target)</div>
                  {pushPreview.reachable ? (
                    <>
                      <div className="text-xs">URL: <span className="font-mono truncate block">{cfg?.peer_url}</span></div>
                      <div className="text-xs">Role: <span className="font-mono">{pushPreview.peer_role || 'unknown'}</span></div>
                      <div className="text-xs">Version: <span className="font-mono">{pushPreview.peer_version || 'unknown'}</span></div>
                    </>
                  ) : (
                    <div className="text-xs text-red-700">Peer unreachable: {pushPreview.peer_error || 'unknown error'}</div>
                  )}
                </div>
              </div>

              {pushPreview.last_push && (
                <div className="text-xs text-gray-600">
                  Last successful push: <span className="font-mono">{pushPreview.last_push.ts}</span> ({(pushPreview.last_push.size / 1024).toFixed(1)} KB).
                </div>
              )}

              <div className="text-xs text-gray-700 bg-slate-50 border border-slate-200 rounded px-3 py-2">
                Type <code className="font-mono font-semibold">push</code> in the field below to confirm.
              </div>
            </div>
          ) : null
        }
      />
    </div>
  )
}
