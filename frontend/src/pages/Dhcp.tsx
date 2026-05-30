// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 MurOS contributors.
import { useEffect, useMemo, useState } from 'react'
import {
  api,
  type DhcpActiveLease,
  type DhcpConfig,
  type DhcpConfigInput,
  type DhcpPool,
  type DhcpPoolInput,
  type DhcpStaticLease,
  type DhcpStaticLeaseInput,
  type DhcpStatus,
  type Interface,
  type RaConfig,
} from '../lib/api'
import PageHeader from '../components/PageHeader'
import CardHeader from '../components/CardHeader'
import Toggle from '../components/Toggle'
import Modal from '../components/Modal'
import EmptyState from '../components/EmptyState'
import ApplyServiceButton from '../components/ApplyServiceButton'
import { isDirty } from '../lib/dirty'
import { ErrorBlock, SuccessBlock } from '../components/Alerts'
import { toast } from '../components/Toast'
import { ServiceStatusInline, type ServiceState } from '../components/ServiceStatusLine'
import { useConfirm } from '../components/ConfirmModal'
import { Network } from 'lucide-react'

// Page /services/dhcp : Kea DHCP-only.
// Layout : status, global config, pools per interface, static reservations,
// live leases from /var/lib/kea/kea-leases4.csv.

export default function DhcpPage() {
  const [status, setStatus] = useState<DhcpStatus | null>(null)
  const [cfg, setCfg] = useState<DhcpConfig | null>(null)
  const [cfgForm, setCfgForm] = useState<DhcpConfigInput | null>(null)
  const [pools, setPools] = useState<DhcpPool[]>([])
  const [leases, setLeases] = useState<DhcpStaticLease[]>([])
  const [active, setActive] = useState<DhcpActiveLease[]>([])
  const [ifaces, setIfaces] = useState<Interface[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [editingPool, setEditingPool] = useState<DhcpPool | null>(null)
  const [creatingPool, setCreatingPool] = useState(false)
  const [editingLease, setEditingLease] = useState<DhcpStaticLease | null>(null)
  const [creatingLease, setCreatingLease] = useState(false)
  const { confirm, ConfirmHost } = useConfirm()

  const ifaceById = useMemo(
    () => Object.fromEntries(ifaces.map((i) => [i.id, i])),
    [ifaces],
  )

  const reload = async () => {
    try {
      const [s, c, p, l, a, i] = await Promise.all([
        api.dhcp.status(),
        api.dhcp.getConfig(),
        api.dhcp.listPools(),
        api.dhcp.listLeases(),
        api.dhcp.activeLeases(),
        api.interfaces.list(),
      ])
      setStatus(s); setCfg(c); setPools(p); setLeases(l); setActive(a); setIfaces(i)
    } catch (e) { setError((e as Error).message) }
  }

  useEffect(() => { void reload() }, [])
  useEffect(() => {
    if (cfg) {
      setCfgForm({
        enabled: cfg.enabled,
        authoritative: cfg.authoritative,
        default_lease_seconds: cfg.default_lease_seconds,
        domain: cfg.domain,
      })
    }
  }, [cfg])
  useEffect(() => {
    const id = setInterval(() => {
      Promise.all([api.dhcp.status(), api.dhcp.activeLeases()])
        .then(([s, a]) => { setStatus(s); setActive(a) })
        .catch(() => {})
    }, 5000)
    return () => clearInterval(id)
  }, [])

  // The Apply button shows the orange pending-changes dot as soon as
  // the in-memory form diverges from the last server snapshot. Apply
  // re-runs reload() so cfg refreshes and dirty resets to false.
  const cfgDirty = isDirty(cfgForm, cfg && {
    enabled: cfg.enabled,
    authoritative: cfg.authoritative,
    default_lease_seconds: cfg.default_lease_seconds,
    domain: cfg.domain,
  })

  // Save persists DB + on-disk Kea config and stages a Kea
  // restart. Apply (page header) is the path that actually restarts
  // Kea.
  const saveConfig = async (data: DhcpConfigInput) => {
    setBusy(true); setError(null); setMessage(null)
    try {
      await api.dhcp.updateConfig(data)
      setMessage('DHCP configuration saved. Click Apply to restart Kea.')
      await reload()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  // Page-header quick toggle : flips the persisted enabled flag and
  // applies immediately (start/stop Kea through the regular apply
  // pipeline). Independent from the form Apply button so the operator
  // can pause the service without saving unrelated edits.
  const toggleService = async () => {
    if (!cfg) return
    const next = !cfg.enabled
    const ok = await confirm(next ? {
      title: 'Enable DHCP server ?',
      message: 'Kea will be started now and at every boot. Clients on configured pools will start receiving leases. Make sure no other DHCP server already serves these networks.',
      confirmLabel: 'Enable',
    } : {
      title: 'Disable DHCP server ?',
      message: 'Kea will be stopped immediately. New clients will not receive any lease until you re-enable it. Existing leases keep working until they expire.',
      confirmLabel: 'Disable',
      destructive: true,
    })
    if (!ok) return
    setBusy(true); setError(null); setMessage(null)
    try {
      await api.dhcp.updateConfig({
        enabled: next,
        authoritative: cfg.authoritative,
        default_lease_seconds: cfg.default_lease_seconds,
        domain: cfg.domain,
      })
      await api.dhcp.apply()
      setMessage(next ? 'DHCP enabled and Kea started.' : 'DHCP disabled and Kea stopped.')
      await reload()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const removePool = async (p: DhcpPool) => {
    const iface = ifaceById[p.interface_id]
    const ok = await confirm({
      title: 'Delete DHCP pool',
      message: `Delete the DHCP pool on '${iface?.name ?? '?'}' (${p.range_start} to ${p.range_end})? All static reservations attached to it will also be removed.`,
      requireText: iface?.name ?? String(p.id),
      confirmLabel: 'Delete',
    })
    if (!ok) return
    try {
      await api.dhcp.deletePool(p.id)
      await reload()
    } catch (e) { setError((e as Error).message) }
  }

  const removeLease = async (l: DhcpStaticLease) => {
    const ok = await confirm({
      title: 'Delete static lease',
      message: `Delete the static reservation for MAC ${l.mac} (${l.ip})?`,
      confirmLabel: 'Delete',
    })
    if (!ok) return
    try {
      await api.dhcp.deleteLease(l.id)
      await reload()
    } catch (e) { setError((e as Error).message) }
  }

  return (
    <div>
      <PageHeader
        icon={<Network size={16} />}
        title="DHCP server"
        description="DHCP server, pools and static leases."
        status={status && (
          <ServiceStatusInline
            state={(status.service_state || 'inactive') as ServiceState}
            version={status.version}
          />
        )}
        serviceEnabled={!!cfg?.enabled}
        serviceToggleBusy={busy || !status?.installed}
        serviceToggleTitle={cfg?.enabled
          ? 'DHCP server enabled. Click to stop Kea and disable it at boot.'
          : 'DHCP server disabled. Click to start Kea and enable it at boot.'}
        onServiceEnabledChange={toggleService}
        actions={
          <ApplyServiceButton
            service="dhcp"
            pendingTooltip="Restart Kea to apply the saved configuration."
            onApplied={() => { void reload(); setMessage('Kea reloaded.') }}
            onError={setError}
            disabled={!status?.installed}
            formDirty={cfgDirty}
          />
        }
      />

      <div className="px-6 py-4 space-y-6">
        {error && <ErrorBlock message={error} />}
        {message && <SuccessBlock message={message} onDismiss={() => setMessage(null)} />}

        {status && !status.installed && (
          <div className="bg-amber-50 border border-amber-200 text-amber-900 px-3 py-3 rounded text-sm">
            <div className="font-medium">Kea is not installed on this node.</div>
            <div className="mt-1">Install it with <code className="font-mono">apt install kea-dhcp4-server</code>, then refresh this page.</div>
          </div>
        )}

        {cfgForm && (
          <ConfigCard
            form={cfgForm}
            setForm={setCfgForm}
            dirty={cfgDirty}
            busy={busy}
            onSave={() => saveConfig(cfgForm)}
          />
        )}

        <section className="card">
          <CardHeader title="DHCP pools">
            <button
              className="btn-primary"
              onClick={() => setCreatingPool(true)}
              disabled={ifaces.length === 0}
              title={ifaces.length === 0 ? 'Configure an interface first' : ''}
            >
              Add pool
            </button>
          </CardHeader>

          {pools.length === 0 ? (
            <EmptyState
              text="No DHCP pool"
              hint="Add a pool bound to a LAN interface to start handing out leases."
              action={
                <button
                  className="btn-primary"
                  onClick={() => setCreatingPool(true)}
                  disabled={ifaces.length === 0}
                  title={ifaces.length === 0 ? 'Configure an interface first' : ''}
                >
                  Add a pool
                </button>
              }
            />
          ) : (
            <div className="border border-gray-200 rounded-md overflow-hidden">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 text-xs uppercase tracking-wider text-gray-600">
                  <tr>
                    <th className="px-3 py-2 text-left">Interface</th>
                    <th className="px-3 py-2 text-left">Range</th>
                    <th className="px-3 py-2 text-left">Gateway</th>
                    <th className="px-3 py-2 text-left">DNS pushed</th>
                    <th className="px-3 py-2 text-left">Lease</th>
                    <th className="px-3 py-2 text-left">State</th>
                    <th className="px-3 py-2 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {pools.map((p) => (
                    <tr key={p.id} className="hover:bg-gray-50">
                      <td className="px-3 py-2 font-mono text-xs">
                        {ifaceById[p.interface_id]?.name ?? `iface#${p.interface_id}`}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs">
                        {p.range_start} to {p.range_end}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-700">
                        {p.gateway || <span className="text-gray-500">auto</span>}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-700">
                        {p.dns_servers || <span className="text-gray-500">self</span>}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-700">
                        {p.lease_seconds ? `${p.lease_seconds}s` : <span className="text-gray-500">default</span>}
                      </td>
                      <td className="px-3 py-2">
                        <span className={`text-xs px-2 py-0.5 rounded font-medium ${
                          p.enabled
                            ? 'bg-emerald-100 text-emerald-800'
                            : 'bg-slate-200 text-slate-700'
                        }`}>
                          {p.enabled ? 'enabled' : 'disabled'}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button className="btn-ghost py-1" onClick={() => setEditingPool(p)}>Edit</button>
                        <button className="btn-ghost py-1 text-red-700 hover:text-red-800" onClick={() => removePool(p)}>Delete</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="card">
          <CardHeader title="Static reservations">
            <button
              className="btn-primary"
              onClick={() => setCreatingLease(true)}
              disabled={pools.length === 0}
              title={pools.length === 0 ? 'Create a DHCP pool first' : ''}
            >
              Add reservation
            </button>
          </CardHeader>

          {leases.length === 0 ? (
            <EmptyState
              text="No static reservation"
              hint="Map a MAC address to a fixed IP so a host always receives the same lease."
              action={
                <button
                  className="btn-primary"
                  onClick={() => setCreatingLease(true)}
                  disabled={pools.length === 0}
                  title={pools.length === 0 ? 'Create a DHCP pool first' : ''}
                >
                  Add a reservation
                </button>
              }
            />
          ) : (
            <div className="border border-gray-200 rounded-md overflow-hidden">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 text-xs uppercase tracking-wider text-gray-600">
                  <tr>
                    <th className="px-3 py-2 text-left">MAC</th>
                    <th className="px-3 py-2 text-left">IP</th>
                    <th className="px-3 py-2 text-left">Hostname</th>
                    <th className="px-3 py-2 text-left">Pool</th>
                    <th className="px-3 py-2 text-left">Comment</th>
                    <th className="px-3 py-2 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {leases.map((l) => {
                    const pool = pools.find((p) => p.id === l.pool_id)
                    const iface = pool ? ifaceById[pool.interface_id] : null
                    return (
                      <tr key={l.id} className="hover:bg-gray-50">
                        <td className="px-3 py-2 font-mono text-xs">{l.mac}</td>
                        <td className="px-3 py-2 font-mono text-xs">{l.ip}</td>
                        <td className="px-3 py-2 text-xs">{l.hostname || <span className="text-gray-500">-</span>}</td>
                        <td className="px-3 py-2 font-mono text-xs">{iface?.name ?? `pool#${l.pool_id}`}</td>
                        <td className="px-3 py-2 text-xs text-gray-700">{l.comment || ''}</td>
                        <td className="px-3 py-2 text-right">
                          <button className="btn-ghost py-1" onClick={() => setEditingLease(l)}>Edit</button>
                          <button className="btn-ghost py-1 text-red-700 hover:text-red-800" onClick={() => removeLease(l)}>Delete</button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="card">
          <CardHeader title={`Active leases (${active.length})`} />
          {active.length === 0 ? (
            <EmptyState
              text="No active lease"
              hint="Clients that requested a lease will appear here. Refreshed every 5 seconds."
            />
          ) : (
            <div className="border border-gray-200 rounded-md overflow-hidden">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 text-xs uppercase tracking-wider text-gray-600">
                  <tr>
                    <th className="px-3 py-2 text-left">MAC</th>
                    <th className="px-3 py-2 text-left">IP</th>
                    <th className="px-3 py-2 text-left">Hostname</th>
                    <th className="px-3 py-2 text-left">Expires</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {active.map((l) => (
                    <tr key={`${l.mac}-${l.ip}`} className="hover:bg-gray-50">
                      <td className="px-3 py-2 font-mono text-xs">{l.mac}</td>
                      <td className="px-3 py-2 font-mono text-xs">{l.ip}</td>
                      <td className="px-3 py-2 text-xs">{l.hostname || <span className="text-gray-500">-</span>}</td>
                      <td className="px-3 py-2 text-xs text-gray-700">{formatExpiry(l.expiry)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <RaCard />
      </div>

      <PoolModal
        open={creatingPool || editingPool !== null}
        pool={editingPool}
        ifaces={ifaces}
        usedIfaceIds={pools.filter((p) => p.id !== editingPool?.id).map((p) => p.interface_id)}
        onClose={() => { setEditingPool(null); setCreatingPool(false) }}
        onSaved={() => { setEditingPool(null); setCreatingPool(false); void reload() }}
      />

      <LeaseModal
        open={creatingLease || editingLease !== null}
        lease={editingLease}
        pools={pools}
        ifaceById={ifaceById}
        onClose={() => { setEditingLease(null); setCreatingLease(false) }}
        onSaved={() => { setEditingLease(null); setCreatingLease(false); void reload() }}
      />

      <ConfirmHost />
    </div>
  )
}

// IPv6 Router Advertisements (radvd) - the IPv6 counterpart of the DHCP
// server. The box advertises a SLAAC prefix derived from the LAN
// interface's own IPv6 address so clients autoconfigure v6.
function RaCard() {
  const [ra, setRa] = useState<RaConfig | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = () => api.ipv6.getRa().then(setRa).catch((e) => setError(String(e)))
  useEffect(() => { void load() }, [])

  const save = async (next: RaConfig) => {
    setBusy(true); setError(null)
    try {
      const res = await api.ipv6.setRa({
        enabled: next.enabled,
        interface: next.interface,
        managed: next.managed,
        other_config: next.other_config,
        advertise_dns: next.advertise_dns,
      })
      setRa(res)
      toast.success('IPv6 Router Advertisements updated')
    } catch (e) { setError(String(e)); void load() } finally { setBusy(false) }
  }

  if (!ra) return null
  return (
    <section className="card">
      <CardHeader title="IPv6 Router Advertisements (SLAAC)" />
      <p className="text-sm text-gray-600 mb-3">
        Advertise the firewall as the IPv6 router so LAN clients autoconfigure
        an IPv6 address (SLAAC). The prefix is taken from the selected
        interface's own IPv6 address. This is the IPv6 counterpart of the DHCP
        server above.
      </p>
      {error && <ErrorBlock message={error} />}
      <div className="flex items-center gap-2 mb-3">
        <Toggle checked={ra.enabled}
          onChange={(v) => save({ ...ra, enabled: v })} disabled={busy} />
        <span className="text-sm font-medium">Enabled</span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <label className="block">
          <div className="text-xs font-medium text-gray-600 mb-1">LAN interface</div>
          <select className="input" value={ra.interface || ''} disabled={busy}
            onChange={(e) => setRa({ ...ra, interface: e.target.value || null })}>
            <option value="">Select an interface...</option>
            {ra.available_interfaces.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          {ra.available_interfaces.length === 0 && (
            <div className="text-xs text-amber-700 mt-1">
              No interface has an IPv6 address yet. Assign one on the Network page.
            </div>
          )}
        </label>
        <div>
          <div className="text-xs font-medium text-gray-600 mb-1">Advertised prefix</div>
          <div className="font-mono text-sm text-gray-900 py-2">
            {ra.prefix || <span className="text-gray-500">none (no IPv6 on this interface)</span>}
          </div>
        </div>
      </div>

      <div className="mt-3 space-y-2">
        <label className="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked={ra.advertise_dns} disabled={busy}
            onChange={(e) => setRa({ ...ra, advertise_dns: e.target.checked })} />
          <span className="text-sm">Advertise this firewall as IPv6 DNS resolver (RDNSS)</span>
        </label>
        <label className="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked={ra.managed} disabled={busy}
            onChange={(e) => setRa({ ...ra, managed: e.target.checked })} />
          <span className="text-sm">Managed (M): clients get their address via DHCPv6 instead of SLAAC</span>
        </label>
        <label className="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked={ra.other_config} disabled={busy}
            onChange={(e) => setRa({ ...ra, other_config: e.target.checked })} />
          <span className="text-sm">Other config (O): clients fetch extra options via DHCPv6</span>
        </label>
      </div>

      <div className="mt-4">
        <button className="btn-apply" disabled={busy} onClick={() => save(ra)}>
          {busy ? 'Applying...' : 'Apply'}
        </button>
      </div>
    </section>
  )
}

function formatExpiry(epoch: number): string {
  if (!epoch) return 'static'
  const d = new Date(epoch * 1000)
  if (d.getTime() < Date.now()) return 'expired'
  return d.toLocaleString()
}

function ConfigCard({ form, setForm, dirty, busy, onSave }: {
  form: DhcpConfigInput
  setForm: (f: DhcpConfigInput) => void
  dirty: boolean
  busy: boolean
  onSave: () => void
}) {
  return (
    <div className="card">
      <div className="flex items-start justify-between gap-3 mb-3">
        <h2 className="text-lg font-semibold">Configuration</h2>
        <button
          type="button"
          className="btn-primary relative"
          onClick={onSave}
          disabled={busy || !dirty}
          title={dirty ? 'Persist the form to disk and stage a reload.' : 'No unsaved change.'}
        >
          {dirty && !busy && (
            <span
              className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-orange-500 ring-2 ring-white"
              aria-hidden="true"
            />
          )}
          {busy ? 'Saving...' : 'Save'}
        </button>
      </div>

      {/* Enable/disable lives on the page header toggle (with a
          confirmation modal). Removed here to avoid two switches. */}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="block">
          <div className="text-sm font-medium mb-1">Default lease (seconds)</div>
          <input type="number" className="input font-mono"
            min={60} max={2592000}
            value={form.default_lease_seconds}
            onChange={(e) => setForm({ ...form, default_lease_seconds: parseInt(e.target.value) || 43200 })} />
          <div className="text-xs text-gray-600 mt-1">12 hours by default. Range 60s to 30 days.</div>
        </label>
        <label className="block">
          <div className="text-sm font-medium mb-1">DNS suffix (optional)</div>
          <input className="input font-mono text-sm"
            placeholder="lan.example.com"
            value={form.domain ?? ''}
            onChange={(e) => setForm({ ...form, domain: e.target.value || null })} />
          <div className="text-xs text-gray-600 mt-1">When set, hostnames are expanded with this suffix.</div>
        </label>
      </div>

      <div className="mt-3 flex items-center gap-2">
        <Toggle checked={form.authoritative} onChange={(v) => setForm({ ...form, authoritative: v })} />
        <span className="text-sm">Authoritative on the subnet</span>
      </div>
      <div className="text-xs text-gray-600 mt-1 ml-12">
        Lets Kea reply DHCPNAK to clients holding a lease from a rogue server.
        Only enable when MurOS is the only DHCP server on the segment.
      </div>
    </div>
  )
}

function PoolModal({ open, pool, ifaces, usedIfaceIds, onClose, onSaved }: {
  open: boolean
  pool: DhcpPool | null
  ifaces: Interface[]
  usedIfaceIds: number[]
  onClose: () => void
  onSaved: () => void
}) {
  const empty: DhcpPoolInput = {
    interface_id: 0,
    range_start: '',
    range_end: '',
    gateway: null,
    dns_servers: null,
    lease_seconds: null,
    enabled: true,
    comment: null,
  }
  const [form, setForm] = useState<DhcpPoolInput>(empty)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)

  useEffect(() => {
    if (!open) return
    setErr(null)
    if (pool) {
      setForm({
        interface_id: pool.interface_id,
        range_start: pool.range_start,
        range_end: pool.range_end,
        gateway: pool.gateway,
        dns_servers: pool.dns_servers,
        lease_seconds: pool.lease_seconds,
        enabled: pool.enabled,
        comment: pool.comment,
      })
    } else {
      setForm(empty)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, pool])

  const availableIfaces = ifaces.filter(
    (i) => !usedIfaceIds.includes(i.id) || i.id === pool?.interface_id,
  )

  const submit = async () => {
    setBusy(true); setErr(null)
    try {
      if (pool) await api.dhcp.updatePool(pool.id, form)
      else await api.dhcp.createPool(form)
      onSaved()
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={pool ? 'Edit DHCP pool' : 'Add DHCP pool'}
      footer={
        <>
          <button className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn-primary" onClick={submit} disabled={busy || form.interface_id === 0}>
            {busy ? 'Saving...' : 'Save'}
          </button>
        </>
      }
    >
      {err && <div className="mb-3"><ErrorBlock message={err} /></div>}
      <div className="space-y-3">
        <label className="block">
          <div className="text-sm font-medium mb-1">Interface</div>
          <select
            className="input"
            value={form.interface_id}
            onChange={(e) => setForm({ ...form, interface_id: parseInt(e.target.value) })}
            disabled={pool !== null}
          >
            <option value={0}>Select an interface...</option>
            {availableIfaces.map((i) => (
              <option key={i.id} value={i.id}>{i.name}</option>
            ))}
          </select>
          <div className="text-xs text-gray-600 mt-1">
            One DHCP pool per interface. Used interfaces are hidden.
          </div>
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <div className="text-sm font-medium mb-1">Range start</div>
            <input className="input font-mono" placeholder="192.168.1.50"
              value={form.range_start}
              onChange={(e) => setForm({ ...form, range_start: e.target.value })} />
          </label>
          <label className="block">
            <div className="text-sm font-medium mb-1">Range end</div>
            <input className="input font-mono" placeholder="192.168.1.250"
              value={form.range_end}
              onChange={(e) => setForm({ ...form, range_end: e.target.value })} />
          </label>
        </div>
        <div className="flex items-center gap-2">
          <Toggle checked={form.enabled} onChange={(v) => setForm({ ...form, enabled: v })} />
          <span className="text-sm">Pool enabled</span>
        </div>

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
            <label className="block">
              <div className="text-sm font-medium mb-1">Gateway pushed to clients</div>
              <input className="input font-mono" placeholder="defaults to the interface IP"
                value={form.gateway ?? ''}
                onChange={(e) => setForm({ ...form, gateway: e.target.value || null })} />
            </label>
            <label className="block">
              <div className="text-sm font-medium mb-1">DNS servers pushed</div>
              <input className="input font-mono" placeholder="defaults to MurOS itself (recommended)"
                value={form.dns_servers ?? ''}
                onChange={(e) => setForm({ ...form, dns_servers: e.target.value || null })} />
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <div className="text-sm font-medium mb-1">Lease (seconds)</div>
                <input type="number" className="input font-mono" placeholder="inherit default"
                  value={form.lease_seconds ?? ''}
                  onChange={(e) => setForm({ ...form, lease_seconds: e.target.value ? parseInt(e.target.value) : null })} />
              </label>
              <label className="block">
                <div className="text-sm font-medium mb-1">Comment</div>
                <input className="input" value={form.comment ?? ''}
                  onChange={(e) => setForm({ ...form, comment: e.target.value || null })} />
              </label>
            </div>
          </div>
        )}
      </div>
    </Modal>
  )
}

function LeaseModal({ open, lease, pools, ifaceById, onClose, onSaved }: {
  open: boolean
  lease: DhcpStaticLease | null
  pools: DhcpPool[]
  ifaceById: Record<number, Interface>
  onClose: () => void
  onSaved: () => void
}) {
  const empty: DhcpStaticLeaseInput = {
    pool_id: pools[0]?.id ?? 0,
    mac: '',
    ip: '',
    hostname: null,
    comment: null,
  }
  const [form, setForm] = useState<DhcpStaticLeaseInput>(empty)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setErr(null)
    if (lease) {
      setForm({
        pool_id: lease.pool_id,
        mac: lease.mac,
        ip: lease.ip,
        hostname: lease.hostname,
        comment: lease.comment,
      })
    } else {
      setForm({ ...empty, pool_id: pools[0]?.id ?? 0 })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, lease, pools])

  const submit = async () => {
    setBusy(true); setErr(null)
    try {
      if (lease) await api.dhcp.updateLease(lease.id, form)
      else await api.dhcp.createLease(form)
      onSaved()
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={lease ? 'Edit reservation' : 'Add static reservation'}
      footer={
        <>
          <button className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn-primary" onClick={submit} disabled={busy || form.pool_id === 0}>
            {busy ? 'Saving...' : 'Save'}
          </button>
        </>
      }
    >
      {err && <div className="mb-3"><ErrorBlock message={err} /></div>}
      <div className="space-y-3">
        <label className="block">
          <div className="text-sm font-medium mb-1">Pool</div>
          <select className="input" value={form.pool_id}
            onChange={(e) => setForm({ ...form, pool_id: parseInt(e.target.value) })}>
            {pools.map((p) => (
              <option key={p.id} value={p.id}>
                {ifaceById[p.interface_id]?.name ?? `pool#${p.id}`} ({p.range_start} to {p.range_end})
              </option>
            ))}
          </select>
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <div className="text-sm font-medium mb-1">MAC</div>
            <input className="input font-mono" placeholder="aa:bb:cc:dd:ee:ff"
              value={form.mac}
              onChange={(e) => setForm({ ...form, mac: e.target.value })} />
          </label>
          <label className="block">
            <div className="text-sm font-medium mb-1">IP</div>
            <input className="input font-mono" placeholder="192.168.1.10"
              value={form.ip}
              onChange={(e) => setForm({ ...form, ip: e.target.value })} />
          </label>
        </div>
        <label className="block">
          <div className="text-sm font-medium mb-1">Hostname (optional)</div>
          <input className="input" value={form.hostname ?? ''}
            onChange={(e) => setForm({ ...form, hostname: e.target.value || null })} />
        </label>
        <label className="block">
          <div className="text-sm font-medium mb-1">Comment (optional)</div>
          <input className="input" value={form.comment ?? ''}
            onChange={(e) => setForm({ ...form, comment: e.target.value || null })} />
        </label>
      </div>
    </Modal>
  )
}
