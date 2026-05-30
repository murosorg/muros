import { useEffect, useState } from 'react'
import {
  api,
  type SnmpStatus,
  type SnmpConfig,
  type SnmpConfigInput,
} from '../lib/api'
import PageHeader from '../components/PageHeader'
import { useConfirm } from '../components/ConfirmModal'
import ApplyServiceButton from '../components/ApplyServiceButton'
import { isDirty } from '../lib/dirty'
import { ErrorBlock, SuccessBlock } from '../components/Alerts'
import { ServiceStatusInline, type ServiceState } from '../components/ServiceStatusLine'
import { Radio } from 'lucide-react'

export default function SNMP() {
  const [status, setStatus] = useState<SnmpStatus | null>(null)
  const [cfg, setCfg] = useState<SnmpConfig | null>(null)
  const [form, setForm] = useState<SnmpConfigInput | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const { confirm, ConfirmHost } = useConfirm()

  useEffect(() => {
    if (cfg) {
      setForm({
        enabled: cfg.enabled, port: cfg.port, community: cfg.community,
        allowed_networks: cfg.allowed_networks, syscontact: cfg.syscontact,
        syslocation: cfg.syslocation,
      })
    }
  }, [cfg])

  const reload = async () => {
    try {
      const [s, c] = await Promise.all([
        api.snmp.status(),
        api.snmp.getConfig(),
      ])
      setStatus(s); setCfg(c)
    } catch (e) { setError((e as Error).message) }
  }

  useEffect(() => { reload() }, [])
  useEffect(() => {
    const id = setInterval(() => {
      api.snmp.status().then(setStatus).catch(() => {})
    }, 5000)
    return () => clearInterval(id)
  }, [])

  const installPkgs = async () => {
    setBusy(true); setError(null); setMessage(null)
    try {
      const r = await api.snmp.install()
      setMessage(r.newly_installed.length > 0
        ? `Installed packages : ${r.newly_installed.join(', ')}.`
        : r.installed ? 'Packages already installed.' : (r.output_tail || 'Dry-run.'))
      await reload()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  // Apply button shows the orange pending-changes dot while the form
  // is out of sync with the last server snapshot.
  const cfgDirty = isDirty(form, cfg && {
    enabled: cfg.enabled, port: cfg.port, community: cfg.community,
    allowed_networks: cfg.allowed_networks, syscontact: cfg.syscontact,
    syslocation: cfg.syslocation,
  })

  // Save persists the form (DB + on-disk snmpd.conf.d) and marks the
  // service dirty. The Apply button in the page header is the only
  // path that actually restarts snmpd.
  const save = async (data: SnmpConfigInput) => {
    setBusy(true); setError(null); setMessage(null)
    try {
      await api.snmp.updateConfig(data)
      setMessage('Configuration saved. Click Apply to restart snmpd.')
      await reload()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  // Page-header toggle : flips the persisted enabled flag AND applies
  // immediately (restart snmpd through the regular apply pipeline). Off
  // -> on after off acts as a restart. Independent from the form Apply
  // button so the operator can pause the service without touching the
  // unsaved form edits.
  const toggleService = async () => {
    if (!cfg) return
    const next = !cfg.enabled
    const ok = await confirm(next ? {
      title: 'Enable SNMP ?',
      message: 'snmpd will be started now and at every boot. Read-only metrics will become reachable on the configured port.',
      confirmLabel: 'Enable',
    } : {
      title: 'Disable SNMP ?',
      message: 'snmpd will be stopped immediately and will not start at boot until you re-enable it. Monitoring tools will lose visibility on this firewall.',
      confirmLabel: 'Disable',
      destructive: true,
    })
    if (!ok) return
    setBusy(true); setError(null); setMessage(null)
    try {
      await api.snmp.updateConfig({
        enabled: next, port: cfg.port, community: cfg.community,
        allowed_networks: cfg.allowed_networks, syscontact: cfg.syscontact,
        syslocation: cfg.syslocation,
      })
      await api.snmp.apply()
      setMessage(next ? 'SNMP enabled and snmpd started.' : 'SNMP disabled and snmpd stopped.')
      await reload()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <div>
      <PageHeader
        icon={<Radio size={16} />}
        title="SNMP"
        description="Read-only SNMPv2c metrics."
        status={status && (
          <ServiceStatusInline
            state={(status.service_state ?? (status.service_active ? 'active' : 'inactive')) as ServiceState}
            version={status.version}
          />
        )}
        serviceEnabled={!!cfg?.enabled}
        serviceToggleBusy={busy || !status?.installed}
        serviceToggleTitle={cfg?.enabled
          ? 'SNMP enabled. Click to stop snmpd and disable it at boot.'
          : 'SNMP disabled. Click to start snmpd and enable it at boot.'}
        onServiceEnabledChange={toggleService}
        actions={
          <ApplyServiceButton
            service="snmp"
            pendingTooltip="Restart snmpd to apply the saved configuration."
            onApplied={() => { void reload(); setMessage('snmpd reloaded.') }}
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
            <div className="flex items-start justify-between gap-3 flex-wrap">
              <div>
                <div className="font-medium">Missing packages on this node</div>
                <div className="mt-1">To install: <code>snmpd snmp</code>.</div>
              </div>
              <button className="btn-primary whitespace-nowrap" onClick={installPkgs} disabled={busy}>
                {busy ? 'Installing...' : 'Install now'}
              </button>
            </div>
          </div>
        )}

        {form && (
          <ConfigPanel
            form={form}
            setForm={setForm}
            dirty={cfgDirty}
            busy={busy}
            onSave={() => save(form)}
          />
        )}
      </div>
      <ConfirmHost />
    </div>
  )
}

function ConfigPanel({ form, setForm, dirty, busy, onSave }: {
  form: SnmpConfigInput
  setForm: (f: SnmpConfigInput) => void
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
          confirmation modal). Removed from here to avoid two switches
          for the same flag. */}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label="UDP port" hint="161 by default">
          <input type="number" className="input" value={form.port}
            onChange={(e) => setForm({ ...form, port: parseInt(e.target.value) || 161 })} />
        </Field>
        <Field label="Community v2c" hint="Read-only community string. Treat it like a password.">
          <input
            className={`input font-mono text-sm ${form.community === 'public' ? 'border-amber-400 ring-1 ring-amber-300' : ''}`}
            value={form.community}
            placeholder="public"
            onChange={(e) => setForm({ ...form, community: e.target.value })}
          />
          {form.community === 'public' && (
            <div className="mt-1 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-1">
              Using <code className="font-mono">public</code> is the default community everyone scans for.
              Change it before exposing this firewall to any untrusted network.
            </div>
          )}
        </Field>

        <Field label="Allowed networks" hint="CIDR list, comma-separated. Default: RFC1918 (private LANs).">
          <textarea
            className="input font-mono text-sm leading-snug"
            rows={2}
            value={form.allowed_networks}
            onChange={(e) => setForm({ ...form, allowed_networks: e.target.value })}
          />
        </Field>
        <Field label="sysContact (OID 1.3.6.1.2.1.1.4)">
          <input className="input" value={form.syscontact}
            onChange={(e) => setForm({ ...form, syscontact: e.target.value })} />
        </Field>

        <Field label="sysLocation (OID 1.3.6.1.2.1.1.6)">
          <input className="input" value={form.syslocation}
            onChange={(e) => setForm({ ...form, syslocation: e.target.value })} />
        </Field>
      </div>

      <div className="mt-4 text-xs text-gray-600 bg-slate-50 border border-slate-200 rounded p-3 space-y-1">
        <div className="font-medium">Test from another machine:</div>
        <code className="block font-mono">snmpwalk -v2c -c {form.community} &lt;ip-firewall&gt; system</code>
        <code className="block font-mono">snmpwalk -v2c -c {form.community} &lt;ip-firewall&gt; ifTable</code>
      </div>

      <div className="mt-4 border border-slate-200 rounded">
        <div className="px-3 py-2 text-xs font-semibold uppercase tracking-wide text-gray-600 bg-slate-50 border-b border-slate-200">
          Exposed OIDs (read-only)
        </div>
        <div className="divide-y divide-slate-100 text-sm">
          {EXPOSED_OIDS.map((g) => (
            <div key={g.mib} className="px-3 py-2">
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="font-medium text-gray-900">{g.mib}</div>
                <div className="font-mono text-[11px] text-gray-500">{g.root}</div>
              </div>
              <div className="text-xs text-gray-600 mt-0.5">{g.desc}</div>
              <ul className="mt-1.5 grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-0.5">
                {g.items.map((it) => (
                  <li key={it.oid} className="text-xs flex items-baseline gap-2">
                    <code className="font-mono text-gray-700 whitespace-nowrap">{it.oid}</code>
                    <span className="text-gray-700 truncate" title={it.label}>{it.label}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>

    </div>
  )
}

const EXPOSED_OIDS: { mib: string; root: string; desc: string; items: { oid: string; label: string }[] }[] = [
  {
    mib: 'SNMPv2-MIB (system)',
    root: '1.3.6.1.2.1.1',
    desc: 'Identity of the node (hostname, contact, location, uptime).',
    items: [
      { oid: 'sysDescr.0',    label: 'OS description (uname -a like)' },
      { oid: 'sysObjectID.0', label: 'Vendor enterprise OID' },
      { oid: 'sysUpTime.0',   label: 'Uptime in 1/100 s' },
      { oid: 'sysContact.0',  label: 'Admin contact (see field above)' },
      { oid: 'sysName.0',     label: 'Hostname' },
      { oid: 'sysLocation.0', label: 'Physical location (see field above)' },
    ],
  },
  {
    mib: 'IF-MIB (interfaces)',
    root: '1.3.6.1.2.1.2 / 1.3.6.1.2.1.31',
    desc: 'Per-interface counters (octets, packets, errors, MTU, speed, oper status).',
    items: [
      { oid: 'ifIndex',         label: 'Internal interface index' },
      { oid: 'ifDescr',         label: 'Name (eth0, vlan100, ...)' },
      { oid: 'ifType',          label: 'Type (ethernet, tunnel, ...)' },
      { oid: 'ifMtu',           label: 'MTU' },
      { oid: 'ifSpeed',         label: 'Link speed (32-bit)' },
      { oid: 'ifOperStatus',    label: 'Operational state (up/down)' },
      { oid: 'ifInOctets',      label: 'Bytes received (32-bit)' },
      { oid: 'ifOutOctets',     label: 'Bytes sent (32-bit)' },
      { oid: 'ifHCInOctets',    label: 'Bytes received (64-bit)' },
      { oid: 'ifHCOutOctets',   label: 'Bytes sent (64-bit)' },
      { oid: 'ifInErrors',      label: 'Receive errors' },
      { oid: 'ifOutErrors',     label: 'Transmit errors' },
    ],
  },
  {
    mib: 'IP-MIB',
    root: '1.3.6.1.2.1.4',
    desc: 'IPv4/IPv6 stack: addresses, routing table, ARP/NDP cache.',
    items: [
      { oid: 'ipAdEntAddr',     label: 'Configured IP addresses' },
      { oid: 'ipRouteDest',     label: 'Routing table entries' },
      { oid: 'ipNetToMediaPhysAddress', label: 'ARP entries' },
    ],
  },
  {
    mib: 'HOST-RESOURCES-MIB',
    root: '1.3.6.1.2.1.25',
    desc: 'CPU, memory, storage, running processes.',
    items: [
      { oid: 'hrSystemUptime.0', label: 'Uptime (host-level)' },
      { oid: 'hrSystemDate.0',   label: 'Local date/time' },
      { oid: 'hrProcessorLoad',  label: 'Per-CPU load (%)' },
      { oid: 'hrStorageUsed',    label: 'Used storage by mount point' },
      { oid: 'hrStorageSize',    label: 'Total storage by mount point' },
      { oid: 'hrMemorySize.0',   label: 'Total RAM' },
      { oid: 'hrSWRunName',      label: 'Running process names' },
    ],
  },
  {
    mib: 'UCD-SNMP-MIB',
    root: '1.3.6.1.4.1.2021',
    desc: 'Net-SNMP extensions: load average, memory details, disk usage.',
    items: [
      { oid: 'laLoad.1 / .2 / .3', label: 'Load average 1/5/15 min' },
      { oid: 'memTotalReal.0',     label: 'Total physical memory (KB)' },
      { oid: 'memAvailReal.0',     label: 'Available physical memory (KB)' },
      { oid: 'memTotalSwap.0',     label: 'Total swap (KB)' },
      { oid: 'memAvailSwap.0',     label: 'Available swap (KB)' },
      { oid: 'ssCpuRawIdle.0',     label: 'CPU idle ticks (use for %CPU)' },
    ],
  },
  {
    mib: 'TCP-MIB / UDP-MIB',
    root: '1.3.6.1.2.1.6 / .7',
    desc: 'TCP/UDP connection counters and current connections.',
    items: [
      { oid: 'tcpCurrEstab.0',   label: 'Established TCP connections' },
      { oid: 'tcpActiveOpens.0', label: 'Active TCP opens since boot' },
      { oid: 'tcpInSegs.0',      label: 'TCP segments received' },
      { oid: 'tcpOutSegs.0',     label: 'TCP segments sent' },
      { oid: 'udpInDatagrams.0', label: 'UDP datagrams received' },
      { oid: 'udpOutDatagrams.0',label: 'UDP datagrams sent' },
    ],
  },
]

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-sm font-medium mb-1">{label}</div>
      {children}
      {hint && <div className="text-xs text-gray-600 mt-1">{hint}</div>}
    </label>
  )
}
