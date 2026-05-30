// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 MurOS contributors.
import { useEffect, useState } from 'react'
import { api, type SyslogStatus, type SyslogConfig, type SyslogConfigInput } from '../lib/api'
import PageHeader from '../components/PageHeader'
import { useConfirm } from '../components/ConfirmModal'
import ApplyServiceButton from '../components/ApplyServiceButton'
import { isDirty } from '../lib/dirty'
import { ErrorBlock, SuccessBlock } from '../components/Alerts'
import { ServiceStatusInline, type ServiceState } from '../components/ServiceStatusLine'
import { Share2 } from 'lucide-react'

function toInput(cfg: SyslogConfig): SyslogConfigInput {
  return {
    enabled: cfg.enabled, host: cfg.host, port: cfg.port,
    protocol: cfg.protocol, format: cfg.format, comment: cfg.comment,
  }
}

export default function Syslog() {
  const [status, setStatus] = useState<SyslogStatus | null>(null)
  const [cfg, setCfg] = useState<SyslogConfig | null>(null)
  const [form, setForm] = useState<SyslogConfigInput | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const { confirm, ConfirmHost } = useConfirm()

  useEffect(() => { if (cfg) setForm(toInput(cfg)) }, [cfg])

  const reload = async () => {
    try {
      const [s, c] = await Promise.all([api.syslog.status(), api.syslog.getConfig()])
      setStatus(s); setCfg(c)
    } catch (e) { setError((e as Error).message) }
  }
  useEffect(() => { void reload() }, [])
  useEffect(() => {
    const id = setInterval(() => { api.syslog.status().then(setStatus).catch(() => {}) }, 5000)
    return () => clearInterval(id)
  }, [])

  const installPkgs = async () => {
    setBusy(true); setError(null); setMessage(null)
    try {
      const r = await api.syslog.install()
      setMessage(r.newly_installed.length > 0
        ? `Installed packages: ${r.newly_installed.join(', ')}.`
        : r.installed ? 'Packages already installed.' : (r.output_tail || 'Dry-run.'))
      await reload()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const cfgDirty = isDirty(form, cfg && toInput(cfg))

  const save = async (data: SyslogConfigInput) => {
    setBusy(true); setError(null); setMessage(null)
    try {
      await api.syslog.updateConfig(data)
      setMessage('Configuration saved. Click Apply to restart rsyslog.')
      await reload()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const toggleService = async () => {
    if (!cfg) return
    const next = !cfg.enabled
    if (next && !cfg.host) { setError('Set a collector host before enabling forwarding.'); return }
    const ok = await confirm(next ? {
      title: 'Enable log forwarding?',
      message: 'rsyslog will forward every log line to the configured collector, now and at every boot.',
      confirmLabel: 'Enable',
    } : {
      title: 'Disable log forwarding?',
      message: 'Logs will stop being shipped to the remote collector. Local logging keeps working.',
      confirmLabel: 'Disable', destructive: true,
    })
    if (!ok) return
    setBusy(true); setError(null); setMessage(null)
    try {
      await api.syslog.updateConfig({ ...toInput(cfg), enabled: next })
      await api.syslog.apply()
      setMessage(next ? 'Forwarding enabled.' : 'Forwarding disabled.')
      await reload()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <div>
      <PageHeader
        icon={<Share2 size={16} />}
        title="Remote syslog"
        description="Forward logs to a central syslog server / SIEM."
        status={status && (
          <ServiceStatusInline
            state={(status.service_state ?? (status.service_active ? 'active' : 'inactive')) as ServiceState}
            version={status.version}
          />
        )}
        serviceEnabled={!!cfg?.enabled}
        serviceToggleBusy={busy || !status?.installed}
        serviceToggleTitle={cfg?.enabled
          ? 'Forwarding enabled. Click to stop shipping logs.'
          : 'Forwarding disabled. Click to start shipping logs.'}
        onServiceEnabledChange={toggleService}
        actions={
          <ApplyServiceButton
            service="syslog"
            pendingTooltip="Restart rsyslog to apply the saved configuration."
            onApplied={() => { void reload(); setMessage('rsyslog reloaded.') }}
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
                <div className="font-medium">Missing package on this node</div>
                <div className="mt-1">To install: <code>rsyslog</code>.</div>
              </div>
              <button className="btn-primary whitespace-nowrap" onClick={installPkgs} disabled={busy}>
                {busy ? 'Installing...' : 'Install now'}
              </button>
            </div>
          </div>
        )}

        {form && (
          <ConfigPanel form={form} setForm={setForm} dirty={cfgDirty} busy={busy} onSave={() => save(form)} />
        )}
      </div>
      <ConfirmHost />
    </div>
  )
}

function ConfigPanel({ form, setForm, dirty, busy, onSave }: {
  form: SyslogConfigInput
  setForm: (f: SyslogConfigInput) => void
  dirty: boolean
  busy: boolean
  onSave: () => void
}) {
  return (
    <div className="card">
      <div className="flex items-start justify-between gap-3 mb-3">
        <h2 className="text-lg font-semibold">Collector</h2>
        <button type="button" className="btn-primary relative" onClick={onSave} disabled={busy || !dirty}
          title={dirty ? 'Persist the form and stage a reload.' : 'No unsaved change.'}>
          {dirty && !busy && (
            <span className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-orange-500 ring-2 ring-white" aria-hidden="true" />
          )}
          {busy ? 'Saving...' : 'Save'}
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label="Collector host" hint="IP or hostname of the syslog server / SIEM.">
          <input className="input" value={form.host} placeholder="10.0.0.5"
            onChange={(e) => setForm({ ...form, host: e.target.value })} />
        </Field>
        <Field label="Port" hint="514 by default (6514 for syslog over TLS-terminating collectors).">
          <input type="number" className="input" value={form.port}
            onChange={(e) => setForm({ ...form, port: parseInt(e.target.value) || 514 })} />
        </Field>
        <Field label="Transport" hint="UDP is fire-and-forget; TCP is reliable (recommended).">
          <select className="input" value={form.protocol}
            onChange={(e) => setForm({ ...form, protocol: e.target.value as 'udp' | 'tcp' })}>
            <option value="udp">UDP</option>
            <option value="tcp">TCP</option>
          </select>
        </Field>
        <Field label="Format" hint="RFC5424 is modern and structured; RFC3164 is legacy BSD.">
          <select className="input" value={form.format}
            onChange={(e) => setForm({ ...form, format: e.target.value as 'rfc5424' | 'rfc3164' })}>
            <option value="rfc5424">RFC5424</option>
            <option value="rfc3164">RFC3164</option>
          </select>
        </Field>
        <Field label="Comment (optional)">
          <input className="input" value={form.comment ?? ''}
            onChange={(e) => setForm({ ...form, comment: e.target.value })} />
        </Field>
      </div>

      <div className="mt-4 text-xs text-gray-600 bg-slate-50 border border-slate-200 rounded p-3">
        Every journald/syslog message on this firewall is forwarded. Logs are buffered on disk
        and retried if the collector is briefly unreachable, so nothing is lost during a short outage.
      </div>
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
