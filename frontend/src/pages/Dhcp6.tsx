// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 MurOS contributors.
import { useEffect, useState } from 'react'
import {
  api, type Dhcp6Status, type Dhcp6Config, type Dhcp6Pool, type Dhcp6PoolInput,
  type Interface,
} from '../lib/api'
import PageHeader from '../components/PageHeader'
import { useConfirm } from '../components/ConfirmModal'
import ApplyServiceButton from '../components/ApplyServiceButton'
import { ErrorBlock, SuccessBlock } from '../components/Alerts'
import { ServiceStatusInline, type ServiceState } from '../components/ServiceStatusLine'
import { Network, Plus, Pencil, Trash2 } from 'lucide-react'

const EMPTY = (interfaceId: number): Dhcp6PoolInput => ({
  interface_id: interfaceId, range_start: '', range_end: '',
  dns_servers: '', lease_seconds: null, enabled: true, comment: '',
})

export default function Dhcp6() {
  const [status, setStatus] = useState<Dhcp6Status | null>(null)
  const [cfg, setCfg] = useState<Dhcp6Config | null>(null)
  const [pools, setPools] = useState<Dhcp6Pool[]>([])
  const [ifaces, setIfaces] = useState<Interface[]>([])
  const [leaseSeconds, setLeaseSeconds] = useState<number>(43200)
  const [editing, setEditing] = useState<Dhcp6Pool | null>(null)
  const [form, setForm] = useState<Dhcp6PoolInput | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const { confirm, ConfirmHost } = useConfirm()

  const reload = async () => {
    try {
      const [s, c, p, i] = await Promise.all([
        api.dhcp6.status(), api.dhcp6.getConfig(), api.dhcp6.pools(), api.interfaces.list(),
      ])
      setStatus(s); setCfg(c); setPools(p); setIfaces(i); setLeaseSeconds(c.default_lease_seconds)
    } catch (e) { setError((e as Error).message) }
  }
  useEffect(() => { void reload() }, [])
  useEffect(() => {
    const id = setInterval(() => { api.dhcp6.status().then(setStatus).catch(() => {}) }, 5000)
    return () => clearInterval(id)
  }, [])

  const ifaceById = Object.fromEntries(ifaces.map((i) => [i.id, i]))
  const leaseDirty = !!cfg && leaseSeconds !== cfg.default_lease_seconds

  const saveLease = async () => {
    if (!cfg) return
    setBusy(true); setError(null); setMessage(null)
    try {
      await api.dhcp6.updateConfig({ enabled: cfg.enabled, default_lease_seconds: leaseSeconds })
      setMessage('Saved. Click Apply to restart the DHCPv6 server.')
      await reload()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const toggleService = async () => {
    if (!cfg) return
    const next = !cfg.enabled
    if (next && pools.length === 0) { setError('Add a pool before enabling the DHCPv6 server.'); return }
    const ok = await confirm(next ? {
      title: 'Enable DHCPv6 server?',
      message: 'kea-dhcp6 will hand out IPv6 addresses on the configured interfaces. Make sure Router Advertisements advertise the Managed (M) flag on those interfaces, otherwise clients will not request a DHCPv6 lease.',
      confirmLabel: 'Enable',
    } : {
      title: 'Disable DHCPv6 server?',
      message: 'The server stops handing out IPv6 leases. Existing leases keep working until they expire.',
      confirmLabel: 'Disable', destructive: true,
    })
    if (!ok) return
    setBusy(true); setError(null); setMessage(null)
    try {
      await api.dhcp6.updateConfig({ enabled: next, default_lease_seconds: cfg.default_lease_seconds })
      await api.dhcp6.apply()
      setMessage(next ? 'DHCPv6 server enabled.' : 'DHCPv6 server disabled.')
      await reload()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const openNew = () => {
    if (ifaces.length === 0) { setError('Configure an interface first.'); return }
    setEditing(null); setForm(EMPTY(ifaces[0].id))
  }
  const openEdit = (p: Dhcp6Pool) => {
    setEditing(p)
    setForm({
      interface_id: p.interface_id, range_start: p.range_start, range_end: p.range_end,
      dns_servers: p.dns_servers ?? '', lease_seconds: p.lease_seconds, enabled: p.enabled,
      comment: p.comment ?? '',
    })
  }
  const closeForm = () => { setForm(null); setEditing(null) }

  const savePool = async () => {
    if (!form) return
    setBusy(true); setError(null); setMessage(null)
    try {
      if (editing) await api.dhcp6.updatePool(editing.id, form)
      else await api.dhcp6.createPool(form)
      setMessage('Pool saved. Click Apply to restart the DHCPv6 server.')
      closeForm(); await reload()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const removePool = async (p: Dhcp6Pool) => {
    const ok = await confirm({
      title: 'Delete this pool?',
      message: `The DHCPv6 range on ${p.interface_name ?? `iface#${p.interface_id}`} will be removed.`,
      confirmLabel: 'Delete', destructive: true,
    })
    if (!ok) return
    setBusy(true); setError(null)
    try { await api.dhcp6.deletePool(p.id); await reload() }
    catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <div>
      <PageHeader
        icon={<Network size={16} />}
        title="DHCPv6 server"
        description="Stateful IPv6 address assignment (Kea DHCPv6)."
        status={status && (
          <ServiceStatusInline
            state={status.service_state as ServiceState}
            version={status.version}
          />
        )}
        serviceEnabled={!!cfg?.enabled}
        serviceToggleBusy={busy || !status?.installed}
        serviceToggleTitle={cfg?.enabled ? 'Server enabled. Click to stop.' : 'Server disabled. Click to start.'}
        onServiceEnabledChange={toggleService}
        actions={
          <ApplyServiceButton
            service="dhcp6"
            pendingTooltip="Restart kea-dhcp6 to apply the saved configuration."
            onApplied={() => { void reload(); setMessage('DHCPv6 server reloaded.') }}
            onError={setError}
            disabled={!status?.installed}
            formDirty={leaseDirty}
          />
        }
      />

      <div className="px-6 py-4 space-y-6">
        {error && <ErrorBlock message={error} />}
        {message && <SuccessBlock message={message} onDismiss={() => setMessage(null)} />}

        {status && !status.installed && (
          <div className="bg-amber-50 border border-amber-200 text-amber-900 px-3 py-3 rounded text-sm">
            kea-dhcp6 is not installed on this node.
          </div>
        )}

        <div className="text-xs text-gray-600 bg-slate-50 border border-slate-200 rounded p-3">
          Stateful DHCPv6 only works when the LAN Router Advertisements set the Managed (M) flag on
          the same interface (see the IPv6 RA page). Otherwise clients keep using SLAAC and ignore DHCPv6.
        </div>

        <div className="card">
          <div className="flex items-end gap-3 flex-wrap">
            <label className="block">
              <div className="text-sm font-medium mb-1">Default lease (seconds)</div>
              <input type="number" className="input w-48" value={leaseSeconds}
                onChange={(e) => setLeaseSeconds(parseInt(e.target.value) || 0)} />
            </label>
            <button className="btn-primary relative" onClick={saveLease} disabled={busy || !leaseDirty}>
              {leaseDirty && !busy && (
                <span className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-orange-500 ring-2 ring-white" aria-hidden="true" />
              )}
              {busy ? 'Saving...' : 'Save'}
            </button>
          </div>
        </div>

        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">Address pools</h2>
            <button className="btn-primary" onClick={openNew} disabled={busy}
              title={ifaces.length === 0 ? 'Configure an interface first' : ''}>
              <Plus size={14} className="mr-1" /> Add pool
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-gray-500 border-b">
                  <th className="py-2 pr-3">Interface</th>
                  <th className="py-2 pr-3">Range</th>
                  <th className="py-2 pr-3">DNS</th>
                  <th className="py-2 pr-3">Enabled</th>
                  <th className="py-2 pr-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {pools.length === 0 && (
                  <tr><td colSpan={5} className="py-6 text-center text-gray-400">No pool yet.</td></tr>
                )}
                {pools.map((p) => (
                  <tr key={p.id} className="border-b last:border-0">
                    <td className="py-2 pr-3 font-medium">{p.interface_name ?? ifaceById[p.interface_id]?.name ?? `iface#${p.interface_id}`}</td>
                    <td className="py-2 pr-3 font-mono text-xs">{p.range_start} - {p.range_end}</td>
                    <td className="py-2 pr-3 font-mono text-xs">{p.dns_servers || 'auto'}</td>
                    <td className="py-2 pr-3">{p.enabled ? 'yes' : 'no'}</td>
                    <td className="py-2 pr-3">
                      <div className="flex items-center justify-end gap-1">
                        <button className="icon-btn" title="Edit" onClick={() => openEdit(p)} disabled={busy}><Pencil size={15} /></button>
                        <button className="icon-btn text-red-600" title="Delete" onClick={() => removePool(p)} disabled={busy}><Trash2 size={15} /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {form && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-40 p-4" onClick={closeForm}>
          <div className="bg-white rounded-lg shadow-xl w-full max-w-lg p-5" onClick={(ev) => ev.stopPropagation()}>
            <h2 className="text-lg font-semibold mb-3">{editing ? 'Edit pool' : 'Add pool'}</h2>
            <div className="space-y-3">
              <Field label="Interface">
                <select className="input" value={form.interface_id}
                  onChange={(e) => setForm({ ...form, interface_id: parseInt(e.target.value) })}>
                  {ifaces.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
                </select>
              </Field>
              <Field label="Range start" hint="First IPv6 address of the pool, e.g. 2001:db8:1::100.">
                <input className="input font-mono" value={form.range_start} placeholder="2001:db8:1::100"
                  onChange={(e) => setForm({ ...form, range_start: e.target.value })} />
              </Field>
              <Field label="Range end" hint="Last IPv6 address; must share the same /64 as the start.">
                <input className="input font-mono" value={form.range_end} placeholder="2001:db8:1::1ff"
                  onChange={(e) => setForm({ ...form, range_end: e.target.value })} />
              </Field>
              <Field label="DNS servers (optional)" hint="Comma-separated IPv6 addresses. Empty = advertise this firewall.">
                <input className="input" value={form.dns_servers ?? ''}
                  onChange={(e) => setForm({ ...form, dns_servers: e.target.value })} />
              </Field>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={form.enabled}
                  onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
                Enabled
              </label>
            </div>
            <div className="flex justify-end gap-2 mt-5">
              <button className="btn-secondary" onClick={closeForm} disabled={busy}>Cancel</button>
              <button className="btn-primary" onClick={savePool} disabled={busy || !form.range_start || !form.range_end}>
                {busy ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}
      <ConfirmHost />
    </div>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-sm font-medium mb-1">{label}</div>
      {children}
      {hint && <div className="text-xs text-gray-600 mt-1">{hint}</div>}
    </label>
  )
}
