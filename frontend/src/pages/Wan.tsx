import { useEffect, useMemo, useState } from 'react'
import { api, Interface, WanGateway, WanActive } from '../lib/api'
import Modal from '../components/Modal'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'
import Toggle from '../components/Toggle'
import TableSkeleton from '../components/TableSkeleton'
import { ErrorBlock } from '../components/Alerts'
import { ServiceStatusInline, type ServiceState } from '../components/ServiceStatusLine'
import { useConfirm } from '../components/ConfirmModal'
import { toast } from '../components/Toast'
import { Cable, Activity } from 'lucide-react'
import { fmt } from '../lib/format'

export default function WanPage() {
  const [items, setItems] = useState<WanGateway[]>([])
  const [ifaces, setIfaces] = useState<Interface[]>([])
  const [active, setActive] = useState<WanActive | null>(null)
  const [status, setStatus] = useState<{ service_state: string; version: string | null } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<WanGateway | null>(null)
  const [creating, setCreating] = useState(false)
  const { confirm, ConfirmHost } = useConfirm()

  const load = async () => {
    try {
      setLoading(true)
      const [g, i, a, s] = await Promise.all([
        api.wan.list(),
        api.interfaces.list(),
        api.wan.active(),
        api.wan.status().catch(() => null),
      ])
      setItems(g)
      setIfaces(i)
      setActive(a)
      setStatus(s)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // Refresh leger toutes les 3s pour suivre le status en live sans
    // surcharger le backend. Si l'utilisateur change de page, l'effet
    // cleanup arrete le polling.
    const t = setInterval(async () => {
      try {
        const [g, a, s] = await Promise.all([
          api.wan.list(),
          api.wan.active(),
          api.wan.status().catch(() => null),
        ])
        setItems(g)
        setActive(a)
        setStatus(s)
      } catch {
        // Silencieux : un poll qui rate ne doit pas spammer l'UI.
      }
    }, 3000)
    return () => clearInterval(t)
  }, [])

  const ifaceById = useMemo(
    () => Object.fromEntries(ifaces.map((i) => [i.id, i])),
    [ifaces],
  )

  const probeNow = async (g: WanGateway) => {
    try {
      const updated = await api.wan.probe(g.id)
      setItems((xs) => xs.map((x) => (x.id === g.id ? updated : x)))
      toast.success(`Probe '${updated.name}': ${updated.status}`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    }
  }

  const remove = async (g: WanGateway) => {
    const ok = await confirm({
      title: 'Delete WAN gateway',
      message: `Delete '${g.name}'? The dedicated routing table will be flushed. If this was the active WAN, traffic will failover to the next priority.`,
      requireText: g.name,
      confirmLabel: 'Delete',
      destructive: true,
    })
    if (!ok) return
    try {
      await api.wan.remove(g.id)
      await load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    }
  }

  const banner = (() => {
    if (!active) return null
    if (active.reason === 'no_gateway') {
      return (
        <div className="text-sm bg-slate-50 border border-slate-200 text-slate-700 rounded px-3 py-2 mb-3">
          No WAN gateway declared. Add one to enable failover monitoring.
        </div>
      )
    }
    if (active.reason === 'all_down') {
      return (
        <div className="text-sm bg-red-50 border border-red-200 text-red-800 rounded px-3 py-2 mb-3">
          All WAN gateways are down. The default route has been removed. Internet egress is currently unavailable.
        </div>
      )
    }
    return (
      <div className="text-sm bg-emerald-50 border border-emerald-200 text-emerald-800 rounded px-3 py-2 mb-3">
        Active WAN: <span className="font-medium">{active.active_name}</span>{' '}
        (priority order, lowest first).
      </div>
    )
  })()

  return (
    <div>
      {/*
        No Apply button on this page: muros-wan-monitor watches the
        wan_gateways table directly and adjusts the default route on
        every probe tick (ICMP). CRUD operations on a gateway take
        effect within one probe interval, no manual reload needed.
        The daemon unit is started/stopped automatically by the
        backend when at least one enabled gateway exists.
      */}
      <PageHeader
        icon={<Cable size={16} />}
        title="WAN gateways"
        description="Multi-WAN with automatic failover."
        status={status && (
          <ServiceStatusInline
            state={(status.service_state || 'inactive') as ServiceState}
            version={status.version}
          />
        )}
      />

      <div className="px-6 py-4">
        {banner}
        {error && <ErrorBlock message={error} />}

        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">Gateways</h2>
            <button className="btn-primary" onClick={() => setCreating(true)}>
              Add gateway
            </button>
          </div>

          {loading ? (
            <TableSkeleton cols={8} rows={3} />
          ) : items.length === 0 ? (
            <EmptyState
              icon={<Cable size={28} />}
              text="No WAN gateway yet"
              hint="Add a first gateway pointing at your ISP. When you add a second one with a different priority, MurOS will failover automatically."
              action={<button className="btn-primary" onClick={() => setCreating(true)}>Add a gateway</button>}
            />
          ) : (
            <table className="w-full text-sm">
              <thead className="text-xs uppercase text-gray-500 border-b">
                <tr>
                  <th className="text-left px-3 py-2">Enabled</th>
                  <th className="text-left px-3 py-2">Name</th>
                  <th className="text-left px-3 py-2">Interface</th>
                  <th className="text-left px-3 py-2">Gateway IP</th>
                  <th className="text-left px-3 py-2">Priority</th>
                  <th className="text-left px-3 py-2">Probe target</th>
                  <th className="text-left px-3 py-2">Status</th>
                  <th className="text-right px-3 py-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {items.map((g) => (
                  <tr key={g.id} className="border-b last:border-0">
                    <td className="px-3 py-2">
                      <Toggle
                        checked={g.enabled}
                        onChange={async (v) => {
                          try {
                            await api.wan.update(g.id, { ...g, enabled: v })
                            await load()
                          } catch (e) {
                            toast.error(e instanceof Error ? e.message : String(e))
                          }
                        }}
                      />
                    </td>
                    <td className="px-3 py-2 font-medium">{g.name}</td>
                    <td className="px-3 py-2 font-mono text-xs">
                      {ifaceById[g.interface_id]?.name || `#${g.interface_id}`}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">{g.gateway}</td>
                    <td className="px-3 py-2">{g.priority}</td>
                    <td className="px-3 py-2 font-mono text-xs">{g.monitoring_target}</td>
                    <td className="px-3 py-2">
                      <StatusBadge gw={g} />
                    </td>
                    <td className="px-3 py-2 text-right space-x-3 whitespace-nowrap">
                      <button
                        className="text-xs text-gray-600 hover:text-gray-900"
                        onClick={() => probeNow(g)}
                        title="Run an ICMP probe now"
                      >
                        Probe
                      </button>
                      <button
                        className="text-xs text-gray-600 hover:text-gray-900"
                        onClick={() => setEditing(g)}
                      >
                        Edit
                      </button>
                      <button
                        className="text-xs text-red-600 hover:text-red-800"
                        onClick={() => remove(g)}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {(creating || editing) && (
        <Modal
          open
          title={editing ? 'Edit WAN gateway' : 'Add WAN gateway'}
          onClose={() => {
            setCreating(false)
            setEditing(null)
          }}
        >
          <WanForm
            initial={editing || undefined}
            interfaces={ifaces}
            onCancel={() => {
              setCreating(false)
              setEditing(null)
            }}
            onSaved={async () => {
              setCreating(false)
              setEditing(null)
              await load()
            }}
          />
        </Modal>
      )}

      <ConfirmHost />
    </div>
  )
}

function StatusBadge({ gw }: { gw: WanGateway }) {
  const map: Record<WanGateway['status'], string> = {
    up: 'bg-emerald-100 text-emerald-800',
    down: 'bg-red-100 text-red-800',
    unknown: 'bg-slate-100 text-slate-700',
  }
  const last = gw.last_probe_at ? fmt.relative(gw.last_probe_at) : 'never'
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full ${map[gw.status]}`}
      title={`Last probe: ${last}`}
    >
      <Activity size={12} />
      {gw.status}
    </span>
  )
}

type FormState = {
  name: string
  interface_id: number | ''
  gateway: string
  priority: number
  monitoring_target: string
  interval_s: number
  failures_threshold: number
  enabled: boolean
  comment: string
}

function WanForm({
  initial,
  interfaces,
  onCancel,
  onSaved,
}: {
  initial?: WanGateway
  interfaces: Interface[]
  onCancel: () => void
  onSaved: () => Promise<void>
}) {
  const [s, setS] = useState<FormState>({
    name: initial?.name || '',
    interface_id: initial?.interface_id ?? '',
    gateway: initial?.gateway || '',
    priority: initial?.priority ?? 100,
    monitoring_target: initial?.monitoring_target || '1.1.1.1',
    interval_s: initial?.interval_s ?? 3,
    failures_threshold: initial?.failures_threshold ?? 3,
    enabled: initial?.enabled ?? true,
    comment: initial?.comment || '',
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [showAdvanced, setShowAdvanced] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErr(null)
    if (!s.name || !s.gateway || s.interface_id === '') {
      setErr('Name, interface and gateway IP are required.')
      return
    }
    const payload = {
      name: s.name,
      interface_id: Number(s.interface_id),
      gateway: s.gateway,
      priority: Number(s.priority),
      monitoring_target: s.monitoring_target,
      interval_s: Number(s.interval_s),
      failures_threshold: Number(s.failures_threshold),
      enabled: s.enabled,
      comment: s.comment || null,
    }
    try {
      setBusy(true)
      if (initial) {
        await api.wan.update(initial.id, payload)
      } else {
        await api.wan.create(payload)
      }
      await onSaved()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      {err && <ErrorBlock message={err} />}

      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <div className="text-xs font-medium text-gray-600 mb-1">Name</div>
          <input
            className="input"
            value={s.name}
            onChange={(e) => setS({ ...s, name: e.target.value })}
            placeholder="WAN1-Orange-Fibre"
          />
        </label>
        <label className="block">
          <div className="text-xs font-medium text-gray-600 mb-1">Interface</div>
          <select
            className="input"
            value={s.interface_id}
            onChange={(e) =>
              setS({
                ...s,
                interface_id: e.target.value ? Number(e.target.value) : '',
              })
            }
          >
            <option value="">-- pick an interface --</option>
            {interfaces.map((i) => (
              <option key={i.id} value={i.id}>
                {i.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <label className="block">
        <div className="text-xs font-medium text-gray-600 mb-1">Gateway IP</div>
        <input
          className="input font-mono"
          value={s.gateway}
          onChange={(e) => setS({ ...s, gateway: e.target.value })}
          placeholder="192.168.1.1"
        />
      </label>

      <label className="flex items-center gap-2">
        <Toggle checked={s.enabled} onChange={(v) => setS({ ...s, enabled: v })} />
        <span className="text-sm">Enabled (probed by the monitor)</span>
      </label>

      <div className="pt-1">
        <button
          type="button"
          className="text-xs text-gray-600 hover:text-gray-900 underline"
          onClick={() => setShowAdvanced((v) => !v)}
        >
          {showAdvanced ? 'Hide advanced settings' : 'Advanced settings'}
        </button>
      </div>

      {showAdvanced && (
        <div className="space-y-3 border-t border-gray-200 pt-3">
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <div className="text-xs font-medium text-gray-600 mb-1">Priority</div>
              <input
                type="number"
                className="input"
                value={s.priority}
                onChange={(e) => setS({ ...s, priority: Number(e.target.value) })}
                min={1}
                max={10000}
              />
              <div className="text-[11px] text-gray-500 mt-1">Lower wins when both up.</div>
            </label>
            <label className="block">
              <div className="text-xs font-medium text-gray-600 mb-1">Monitoring target</div>
              <input
                className="input font-mono"
                value={s.monitoring_target}
                onChange={(e) => setS({ ...s, monitoring_target: e.target.value })}
                placeholder="1.1.1.1"
              />
              <div className="text-[11px] text-gray-500 mt-1">IP only. Pick one not on either ISP.</div>
            </label>
            <label className="block">
              <div className="text-xs font-medium text-gray-600 mb-1">Probe interval (s)</div>
              <input
                type="number"
                className="input"
                value={s.interval_s}
                onChange={(e) => setS({ ...s, interval_s: Number(e.target.value) })}
                min={1}
                max={60}
              />
            </label>
            <label className="block">
              <div className="text-xs font-medium text-gray-600 mb-1">Failures before down</div>
              <input
                type="number"
                className="input"
                value={s.failures_threshold}
                onChange={(e) =>
                  setS({ ...s, failures_threshold: Number(e.target.value) })
                }
                min={1}
                max={20}
              />
            </label>
          </div>

          <label className="block">
            <div className="text-xs font-medium text-gray-600 mb-1">Comment</div>
            <input
              className="input"
              value={s.comment}
              onChange={(e) => setS({ ...s, comment: e.target.value })}
              placeholder="Optional"
            />
          </label>
        </div>
      )}

      <div className="flex justify-end gap-2 pt-2">
        <button type="button" className="btn-secondary" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? 'Saving...' : initial ? 'Save' : 'Add gateway'}
        </button>
      </div>
    </form>
  )
}
