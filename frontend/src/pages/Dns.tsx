// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 MurOS contributors.
import { useEffect, useState } from 'react'
import {
  api,
  type DnsLocalRecord,
  type DnsLocalRecordInput,
  type DnsServerConfig,
  type DnsServerConfigInput,
  type DnsServerStatus,
} from '../lib/api'
import PageHeader from '../components/PageHeader'
import CardHeader from '../components/CardHeader'
import Toggle from '../components/Toggle'
import Modal from '../components/Modal'
import EmptyState from '../components/EmptyState'
import ApplyServiceButton from '../components/ApplyServiceButton'
import { isDirty } from '../lib/dirty'
import { ErrorBlock, SuccessBlock } from '../components/Alerts'
import { ServiceStatusInline, type ServiceState } from '../components/ServiceStatusLine'
import { useConfirm } from '../components/ConfirmModal'
import { Globe } from 'lucide-react'
import { useNavigate, useParams } from 'react-router-dom'

// Page /services/dns : recursive DNS resolver (Unbound).
//
// Two tabs:
// - /services/dns/server  : Unbound configuration (mode, ACL, DNSSEC,
//   system resolver toggle).
// - /services/dns/records : local A/AAAA/CNAME/MX/TXT/SRV/PTR records
//   served only on the LAN.
type DnsTab = 'server' | 'records'

const DNS_TABS: { key: DnsTab; label: string }[] = [
  { key: 'server', label: 'Server' },
  { key: 'records', label: 'Records' },
]

function DnsTabs({ tab, onChange }: { tab: DnsTab; onChange: (t: DnsTab) => void }) {
  return (
    <div className="flex border-b border-gray-200">
      {DNS_TABS.map((t) => {
        const active = tab === t.key
        return (
          <button
            key={t.key}
            onClick={() => onChange(t.key)}
            className={`px-3 py-2 text-sm -mb-px border-b-2 transition-colors ${
              active
                ? 'border-steel-400 text-gray-900 font-medium'
                : 'border-transparent text-gray-600 hover:text-gray-900'
            }`}
          >
            {t.label}
          </button>
        )
      })}
    </div>
  )
}

export default function DnsPage() {
  const params = useParams<{ tab?: string }>()
  const nav = useNavigate()
  const validTabs: DnsTab[] = ['server', 'records']
  const tab: DnsTab = (params.tab && (validTabs as string[]).includes(params.tab))
    ? (params.tab as DnsTab)
    : 'server'
  useEffect(() => {
    if (!params.tab || !(validTabs as string[]).includes(params.tab)) {
      nav('/services/dns/server', { replace: true })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.tab])
  const setTab = (k: DnsTab) => nav(`/services/dns/${k}`)
  const [status, setStatus] = useState<DnsServerStatus | null>(null)
  const [cfg, setCfg] = useState<DnsServerConfig | null>(null)
  const [cfgForm, setCfgForm] = useState<DnsServerConfigInput | null>(null)
  const [records, setRecords] = useState<DnsLocalRecord[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [editingRecord, setEditingRecord] = useState<DnsLocalRecord | null>(null)
  const [creatingRecord, setCreatingRecord] = useState(false)
  const { confirm, ConfirmHost } = useConfirm()

  const reload = async () => {
    try {
      const [s, c, r] = await Promise.all([
        api.dnsServer.status(),
        api.dnsServer.getConfig(),
        api.dnsServer.listRecords(),
      ])
      setStatus(s); setCfg(c); setRecords(r)
    } catch (e) { setError((e as Error).message) }
  }

  useEffect(() => { void reload() }, [])
  useEffect(() => {
    if (cfg) {
      setCfgForm({
        enabled: cfg.enabled,
        allow_query_cidrs: cfg.allow_query_cidrs,
        dnssec: cfg.dnssec,
        prefetch: cfg.prefetch,
        forwarders: cfg.forwarders,
        use_as_system_resolver: cfg.use_as_system_resolver,
        register_dhcp_leases: cfg.register_dhcp_leases,
        lease_domain: cfg.lease_domain,
      })
    }
  }, [cfg])
  useEffect(() => {
    const id = setInterval(() => {
      api.dnsServer.status().then(setStatus).catch(() => {})
    }, 5000)
    return () => clearInterval(id)
  }, [])

  // Orange dot on Apply while the form diverges from the last server load.
  const cfgDirty = isDirty(cfgForm, cfg && {
    enabled: cfg.enabled,
    allow_query_cidrs: cfg.allow_query_cidrs,
    dnssec: cfg.dnssec,
    prefetch: cfg.prefetch,
    forwarders: cfg.forwarders,
    use_as_system_resolver: cfg.use_as_system_resolver,
    register_dhcp_leases: cfg.register_dhcp_leases,
    lease_domain: cfg.lease_domain,
  })

  // Save persists DB + on-disk unbound.conf and stages an unbound
  // restart. Apply (page header) is the path that actually restarts
  // unbound.
  const saveConfig = async (data: DnsServerConfigInput) => {
    setBusy(true); setError(null); setMessage(null)
    try {
      await api.dnsServer.updateConfig(data)
      setMessage('DNS configuration saved. Click Apply to restart unbound.')
      await reload()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  // Page-header quick toggle : flips the persisted enabled flag and
  // applies immediately. Independent from the form Apply button.
  const toggleService = async () => {
    if (!cfg) return
    const next = !cfg.enabled
    const ok = await confirm(next ? {
      title: 'Enable DNS server ?',
      message: 'unbound will be started now and at every boot. Recursive resolution will become reachable from allowed networks.',
      confirmLabel: 'Enable',
    } : {
      title: 'Disable DNS server ?',
      message: 'unbound will be stopped immediately. Hosts using this firewall as their DNS resolver will lose name resolution until you re-enable it.',
      confirmLabel: 'Disable',
      destructive: true,
    })
    if (!ok) return
    setBusy(true); setError(null); setMessage(null)
    try {
      await api.dnsServer.updateConfig({
        enabled: next,
        allow_query_cidrs: cfg.allow_query_cidrs,
        dnssec: cfg.dnssec,
        prefetch: cfg.prefetch,
        forwarders: cfg.forwarders,
        use_as_system_resolver: cfg.use_as_system_resolver,
        register_dhcp_leases: cfg.register_dhcp_leases,
        lease_domain: cfg.lease_domain,
      })
      await api.dnsServer.apply()
      setMessage(next ? 'DNS server enabled and unbound started.' : 'DNS server disabled and unbound stopped.')
      await reload()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const removeRecord = async (r: DnsLocalRecord) => {
    const ok = await confirm({
      title: 'Delete local record',
      message: `Delete the local record '${r.name}' (${r.record_type} ${r.value})?`,
      confirmLabel: 'Delete',
    })
    if (!ok) return
    try {
      await api.dnsServer.deleteRecord(r.id)
      await reload()
    } catch (e) { setError((e as Error).message) }
  }

  return (
    <div>
      <PageHeader
        icon={<Globe size={16} />}
        title="DNS server"
        description="Recursive DNS resolver and local records."
        status={status && (
          <ServiceStatusInline
            state={(status.service_state || 'inactive') as ServiceState}
            version={status.version}
          />
        )}
        serviceEnabled={!!cfg?.enabled}
        serviceToggleBusy={busy || !status?.installed}
        serviceToggleTitle={cfg?.enabled
          ? 'DNS server enabled. Click to stop unbound and disable it at boot.'
          : 'DNS server disabled. Click to start unbound and enable it at boot.'}
        onServiceEnabledChange={toggleService}
        actions={
          <ApplyServiceButton
            service="dns"
            pendingTooltip="Restart unbound to apply the saved configuration."
            onApplied={() => { void reload(); setMessage('unbound reloaded.') }}
            onError={setError}
            disabled={!status?.installed}
            formDirty={cfgDirty}
          />
        }
      />

      <div className="px-6 pt-3">
        <DnsTabs tab={tab} onChange={setTab} />
      </div>

      <div className="px-6 py-4 space-y-6">
        {error && <ErrorBlock message={error} onDismiss={() => setError(null)} />}
        {message && <SuccessBlock message={message} onDismiss={() => setMessage(null)} />}

        {tab === 'server' && status && !status.installed && (
          <div className="bg-amber-50 border border-amber-200 text-amber-900 px-3 py-3 rounded text-sm">
            <div className="font-medium">unbound is not installed on this node.</div>
            <div className="mt-1">Install it with <code className="font-mono">apt install unbound unbound-anchor</code>, then refresh this page.</div>
          </div>
        )}

        {tab === 'server' && status && status.system_resolver_active && (
          <div className="text-xs text-slate-500 -mt-2 flex items-center gap-1.5">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-indigo-400"></span>
            Acting as the firewall system resolver (<code className="font-mono">/etc/resolv.conf</code> &rarr; 127.0.0.1).
          </div>
        )}

        {tab === 'server' && cfgForm && (
          <ConfigCard
            form={cfgForm}
            setForm={setCfgForm}
            dirty={cfgDirty}
            busy={busy}
            onSave={() => saveConfig(cfgForm)}
          />
        )}

        {tab === 'records' && (
        <section className="card">
          <CardHeader title="Local DNS records">
            <button className="btn-primary" onClick={() => setCreatingRecord(true)}>
              Add record
            </button>
          </CardHeader>

          {records.length === 0 ? (
            <EmptyState
              text="No local record"
              hint="Map names to IPs served only on the LAN, without touching public DNS."
              action={<button className="btn-primary" onClick={() => setCreatingRecord(true)}>Add a record</button>}
            />
          ) : (
            <div className="border border-gray-200 rounded-md overflow-hidden">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 text-xs uppercase tracking-wider text-gray-600">
                  <tr>
                    <th className="px-3 py-2 text-left">Type</th>
                    <th className="px-3 py-2 text-left">Name</th>
                    <th className="px-3 py-2 text-left">Value</th>
                    <th className="px-3 py-2 text-left">Comment</th>
                    <th className="px-3 py-2 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {records.map((r) => (
                    <tr key={r.id} className="hover:bg-gray-50">
                      <td className="px-3 py-2 font-mono text-xs">{r.record_type}</td>
                      <td className="px-3 py-2 font-mono text-xs">{r.name}</td>
                      <td className="px-3 py-2 font-mono text-xs">{r.value}</td>
                      <td className="px-3 py-2 text-xs text-gray-700">{r.comment || ''}</td>
                      <td className="px-3 py-2 text-right">
                        <button className="btn-ghost py-1" onClick={() => setEditingRecord(r)}>Edit</button>
                        <button className="btn-ghost py-1 text-red-700 hover:text-red-800" onClick={() => removeRecord(r)}>Delete</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        )}
      </div>

      <RecordModal
        open={creatingRecord || editingRecord !== null}
        record={editingRecord}
        onClose={() => { setEditingRecord(null); setCreatingRecord(false) }}
        onSaved={() => { setEditingRecord(null); setCreatingRecord(false); void reload() }}
      />

      <ConfirmHost />
    </div>
  )
}

function ConfigCard({ form, setForm, dirty, busy, onSave }: {
  form: DnsServerConfigInput
  setForm: (f: DnsServerConfigInput) => void
  dirty: boolean
  busy: boolean
  onSave: () => void
}) {
  const mode: 'recursive' | 'forwarding' = (form.forwarders ?? '').trim() ? 'forwarding' : 'recursive'

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
          confirmation modal). Removed from here to avoid two switches. */}

      <div className="mb-4">
        <div className="text-sm font-medium mb-2">Resolution mode</div>
        <div className="flex flex-col gap-2">
          <label className="flex items-start gap-2 cursor-pointer">
            <input type="radio" className="mt-1" checked={mode === 'recursive'}
              onChange={() => setForm({ ...form, forwarders: null })} />
            <div>
              <div className="text-sm font-medium">Recursive (root servers)</div>
              <div className="text-xs text-gray-600">Unbound queries the DNS root hierarchy directly. Recommended when outbound port 53 is open.</div>
            </div>
          </label>
          <label className="flex items-start gap-2 cursor-pointer">
            <input type="radio" className="mt-1" checked={mode === 'forwarding'}
              onChange={() => setForm({ ...form, forwarders: form.forwarders ?? '1.1.1.1,9.9.9.9' })} />
            <div className="flex-1">
              <div className="text-sm font-medium">Forwarding</div>
              <div className="text-xs text-gray-600 mb-1">Send every query to the listed upstream resolvers. Useful behind a corporate DNS or an ISP that blocks outbound :53.</div>
              {mode === 'forwarding' && (
                <input className="input font-mono text-sm w-full"
                  placeholder="1.1.1.1, 9.9.9.9"
                  value={form.forwarders ?? ''}
                  onChange={(e) => setForm({ ...form, forwarders: e.target.value })} />
              )}
            </div>
          </label>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="text-xs text-gray-600 self-center">
          The resolver listens on every interface. Which networks may
          query it is controlled by your firewall rules, not here.
        </div>
        <div className="flex flex-col gap-2 pt-6">
          <label className="flex items-center gap-2">
            <Toggle checked={form.dnssec} onChange={(v) => setForm({ ...form, dnssec: v })} />
            <span className="text-sm">DNSSEC validation</span>
          </label>
          <label className="flex items-center gap-2">
            <Toggle checked={form.prefetch} onChange={(v) => setForm({ ...form, prefetch: v })} />
            <span className="text-sm">Prefetch popular records</span>
          </label>
        </div>
      </div>

      <div className="mt-5 border-t border-gray-200 pt-4">
        <div className="flex items-start gap-2">
          <Toggle checked={form.use_as_system_resolver}
            onChange={(v) => setForm({ ...form, use_as_system_resolver: v })} />
          <div>
            <div className="text-sm font-medium">Use as system resolver</div>
            <div className="text-xs text-gray-600 mt-1 max-w-2xl">
              When enabled, MurOS itself sends its DNS queries to Unbound on 127.0.0.1. A non-loopback fallback (first forwarder, or 1.1.1.1) is appended to <code className="font-mono">/etc/resolv.conf</code> so apt and curl keep working if Unbound is stopped. A backup of the previous resolv.conf is kept and restored when this toggle is turned off.
            </div>
          </div>
        </div>
      </div>

      <div className="mt-5 border-t border-gray-200 pt-4">
        <div className="flex items-start gap-2">
          <Toggle checked={form.register_dhcp_leases}
            onChange={(v) => setForm({ ...form, register_dhcp_leases: v })} />
          <div className="flex-1">
            <div className="text-sm font-medium">Register DHCP hosts in DNS</div>
            <div className="text-xs text-gray-600 mt-1 max-w-2xl">
              Publish DHCP reservations and active leases as local DNS
              records, so LAN clients resolve each other by name (e.g.{' '}
              <code className="font-mono">nas.{form.lease_domain || 'lan'}</code>).
              Manual local records always take precedence.
            </div>
            {form.register_dhcp_leases && (
              <label className="block mt-2 max-w-xs">
                <div className="text-xs font-medium text-gray-600 mb-1">Lease domain</div>
                <input
                  className="input font-mono text-sm"
                  value={form.lease_domain}
                  onChange={(e) => setForm({ ...form, lease_domain: e.target.value })}
                  placeholder="lan"
                />
              </label>
            )}
          </div>
        </div>
      </div>

      <div className="mt-5 text-xs text-gray-600 bg-slate-50 border border-slate-200 rounded p-3 space-y-1">
        <div className="font-medium text-gray-700">Built-in safety net</div>
        <div>An allowlist of critical domains (debian.org, github.com, muros.org, letsencrypt.org and others) is always emitted as <code className="font-mono">local-zone transparent</code>, so future blocklists cannot accidentally break package updates or release polling.</div>
      </div>
    </div>
  )
}

function RecordModal({ open, record, onClose, onSaved }: {
  open: boolean
  record: DnsLocalRecord | null
  onClose: () => void
  onSaved: () => void
}) {
  const empty: DnsLocalRecordInput = { record_type: 'A', name: '', value: '', comment: null }
  const [form, setForm] = useState<DnsLocalRecordInput>(empty)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setErr(null)
    if (record) {
      setForm({
        record_type: record.record_type,
        name: record.name,
        value: record.value,
        comment: record.comment,
      })
    } else {
      setForm(empty)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, record])

  const submit = async () => {
    setBusy(true); setErr(null)
    try {
      if (record) await api.dnsServer.updateRecord(record.id, form)
      else await api.dnsServer.createRecord(form)
      onSaved()
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={record ? 'Edit local record' : 'Add local record'}
      footer={
        <>
          <button className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn-primary" onClick={submit} disabled={busy || !form.name || !form.value}>
            {busy ? 'Saving...' : 'Save'}
          </button>
        </>
      }
    >
      {err && <div className="mb-3"><ErrorBlock message={err} /></div>}
      <div className="space-y-3">
        <div className="grid grid-cols-3 gap-3">
          <label className="block">
            <div className="text-sm font-medium mb-1">Type</div>
            <select className="input" value={form.record_type}
              onChange={(e) => setForm({ ...form, record_type: e.target.value as DnsLocalRecordInput['record_type'] })}>
              <option value="A">A (IPv4)</option>
              <option value="AAAA">AAAA (IPv6)</option>
              <option value="CNAME">CNAME (alias)</option>
              <option value="TXT">TXT (free text / SPF / verification)</option>
              <option value="MX">MX (mail)</option>
              <option value="SRV">SRV (service)</option>
              <option value="PTR">PTR (reverse)</option>
            </select>
          </label>
          <label className="block col-span-2">
            <div className="text-sm font-medium mb-1">Name</div>
            <input className="input font-mono"
              placeholder={
                form.record_type === 'SRV' ? '_sip._tcp.example.com' :
                form.record_type === 'PTR' ? '10.1.168.192.in-addr.arpa' :
                'nas.lan.example.com'
              }
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </label>
        </div>
        <label className="block">
          <div className="text-sm font-medium mb-1">Value</div>
          <input className="input font-mono"
            placeholder={
              form.record_type === 'A' ? '192.168.1.10' :
              form.record_type === 'AAAA' ? '2001:db8::1' :
              form.record_type === 'CNAME' ? 'target.example.com' :
              form.record_type === 'TXT' ? 'v=spf1 -all' :
              form.record_type === 'MX' ? '10 mail.example.com' :
              form.record_type === 'SRV' ? '10 5 5060 sip.example.com' :
              form.record_type === 'PTR' ? 'host.example.com' :
              ''
            }
            value={form.value}
            onChange={(e) => setForm({ ...form, value: e.target.value })} />
          <div className="text-xs text-gray-600 mt-1">
            {form.record_type === 'TXT' && 'Quotes are added automatically. To split a long string, type the parts already quoted: "part1" "part2".'}
            {form.record_type === 'MX' && 'Priority before the target. Lower number wins. 10 is a sensible default.'}
            {form.record_type === 'SRV' && 'Format: priority weight port target. Name should be _service._proto.domain.'}
            {form.record_type === 'CNAME' && 'Cannot coexist with any other record type at the same name.'}
            {form.record_type === 'PTR' && 'Name must be in the .in-addr.arpa / .ip6.arpa form.'}
          </div>
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
