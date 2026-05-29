import { useEffect, useMemo, useState } from 'react'
import {
  api,
  type NotificationConfig,
  type NotificationConfigInput,
  type NotificationRule,
  type NotificationLog,
} from '../lib/api'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'
import Toggle from '../components/Toggle'
import { useConfirm } from '../components/ConfirmModal'
import FormActions from '../components/FormActions'
import { isDirty } from '../lib/dirty'
import CardHeader from '../components/CardHeader'
import { ServiceStatusInline, type ServiceState } from '../components/ServiceStatusLine'
import { ErrorBlock } from '../components/Alerts'
import { toast } from '../components/Toast'
import { fmt } from '../lib/format'
import { Bell, BellOff, SearchX } from 'lucide-react'

export default function Notifications() {
  const [cfg, setCfg] = useState<NotificationConfig | null>(null)
  const [cfgForm, setCfgForm] = useState<NotificationConfigInput | null>(null)
  const [rules, setRules] = useState<NotificationRule[]>([])
  const [logs, setLogs] = useState<NotificationLog[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [logFilter, setLogFilter] = useState('')
  const { confirm, ConfirmHost } = useConfirm()
  const [status, setStatus] = useState<{ service_state: string; version: string | null } | null>(null)

  // Filtre live de l'historique : grep sur event_type + sujet. Utile quand
  // l'historique grossit (50 derniers, mais on peut chercher une alerte
  // specifique par mot-cle ou par type d'event).
  const filteredLogs = useMemo(() => {
    if (!logFilter.trim()) return logs
    const needle = logFilter.toLowerCase()
    return logs.filter((l) =>
      (l.event_type || '').toLowerCase().includes(needle) ||
      (l.subject || '').toLowerCase().includes(needle)
    )
  }, [logs, logFilter])


  const reload = async () => {
    try {
      const [c, r, l, s] = await Promise.all([
        api.notifications.getConfig(),
        api.notifications.listRules(),
        api.notifications.getLog(),
        api.notifications.status().catch(() => null),
      ])
      setCfg(c); setRules(r); setLogs(l); setStatus(s)
    } catch (e) { setError((e as Error).message) }
  }

  useEffect(() => { reload() }, [])
  useEffect(() => {
    if (cfg) {
      setCfgForm({
        enabled: cfg.enabled, smtp_host: cfg.smtp_host, smtp_port: cfg.smtp_port,
        smtp_user: cfg.smtp_user, smtp_password: cfg.smtp_password,
        use_tls: cfg.use_tls, from_addr: cfg.from_addr, to_addrs: cfg.to_addrs,
      })
    }
  }, [cfg])
  useEffect(() => {
    const id = setInterval(() => {
      api.notifications.status().then(setStatus).catch(() => {})
    }, 5000)
    return () => clearInterval(id)
  }, [])

  // Apply button orange dot while form is out of sync with server.
  const cfgDirty = isDirty(cfgForm, cfg && {
    enabled: cfg.enabled, smtp_host: cfg.smtp_host, smtp_port: cfg.smtp_port,
    smtp_user: cfg.smtp_user, smtp_password: cfg.smtp_password,
    use_tls: cfg.use_tls, from_addr: cfg.from_addr, to_addrs: cfg.to_addrs,
  })

  const save = async (data: NotificationConfigInput) => {
    setBusy(true); setError(null)
    try {
      const c = await api.notifications.updateConfig(data)
      setCfg(c)
      toast.success('Configuration saved.')
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  // Page-header quick toggle : flips the persisted enabled flag. The
  // backend mirrors this flag onto the muros-watcher.service systemd
  // unit (enable+now / disable+now), so flipping the toggle here is the
  // right and only top-level switch needed to start/stop the alert
  // pipeline.
  const toggleService = async () => {
    if (!cfg) return
    const next = !cfg.enabled
    const ok = await confirm(next ? {
      title: 'Enable notifications ?',
      message: 'Alert emails will be sent on critical events as soon as the SMTP relay is reachable.',
      confirmLabel: 'Enable',
    } : {
      title: 'Disable notifications ?',
      message: 'No alert email will be sent until you re-enable notifications. Critical events will only be visible in the local logs.',
      confirmLabel: 'Disable',
      destructive: true,
    })
    if (!ok) return
    setBusy(true); setError(null)
    try {
      const c = await api.notifications.updateConfig({
        enabled: next,
        smtp_host: cfg.smtp_host, smtp_port: cfg.smtp_port,
        smtp_user: cfg.smtp_user, smtp_password: cfg.smtp_password,
        use_tls: cfg.use_tls, from_addr: cfg.from_addr, to_addrs: cfg.to_addrs,
      })
      setCfg(c)
      toast.success(next ? 'Notifications enabled.' : 'Notifications disabled, no email will be sent.')
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const sendTest = async () => {
    setBusy(true); setError(null)
    try {
      const r = await api.notifications.sendTest()
      if (r.sent) {
        toast.success("Test email sent. Check the inbox and the history below.")
      } else {
        toast.error(`Failed: ${r.reason || 'unknown reason'}`)
      }
      await reload()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const toggleRule = async (rule: NotificationRule, enabled: boolean) => {
    try {
      await api.notifications.updateRule(rule.id, {
        enabled, throttle_minutes: rule.throttle_minutes,
      })
      await reload()
    } catch (e) { setError((e as Error).message) }
  }

  const setThrottle = async (rule: NotificationRule, minutes: number) => {
    try {
      await api.notifications.updateRule(rule.id, {
        enabled: rule.enabled, throttle_minutes: minutes,
      })
      await reload()
    } catch (e) { setError((e as Error).message) }
  }

  return (
    <div>
      <PageHeader
        icon={<Bell size={16} />}
        title="Notifications"
        description="Email alerts on critical events."
        status={status && (
          <ServiceStatusInline
            state={(status.service_state || 'inactive') as ServiceState}
            version={status.version}
          />
        )}
        serviceEnabled={!!cfg?.enabled}
        serviceToggleBusy={busy}
        serviceToggleTitle={cfg?.enabled
          ? 'Notifications enabled. Click to stop sending alert emails.'
          : 'Notifications disabled. Click to start sending alert emails.'}
        onServiceEnabledChange={toggleService}
        actions={cfgForm && <FormActions onApply={() => save(cfgForm)} busy={busy} dirty={cfgDirty} />}
      />

      <div className="px-6 py-4 space-y-6">
        {error && <ErrorBlock message={error} />}

        {cfg && cfgForm && <SmtpPanel cfg={cfg} form={cfgForm} setForm={setCfgForm} onTest={sendTest} busy={busy} />}

        <div className="card">
          <h2 className="text-lg font-semibold mb-3">Alert rules</h2>
          {rules.length === 0 ? (
            <EmptyState
                  icon={<Bell size={20} />}
                  text="No notification rule" hint="Defaults (critical alerts) are created automatically at startup. Add a rule to customize." />
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-gray-600 border-b">
                <tr>
                  <th className="py-2">Event</th>
                  <th>Throttle (min)</th>
                  <th>Enabled</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((r) => (
                  <tr key={r.id} className="border-b last:border-0">
                    <td className="py-2">
                      <div className="font-mono text-xs">{r.event_type}</div>
                      {r.description && <div className="text-xs text-gray-600">{r.description}</div>}
                    </td>
                    <td>
                      <input type="number" className="input w-24"
                        value={r.throttle_minutes}
                        onChange={(e) => setThrottle(r, parseInt(e.target.value) || 0)} />
                    </td>
                    <td>
                      <Toggle checked={r.enabled}
                        onChange={(v) => toggleRule(r, v)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card">
          <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
            <h2 className="text-lg font-semibold">History (last 50)</h2>
            <div className="flex items-center gap-2">
              <input
                className="input py-1.5 text-sm max-w-xs"
                placeholder="Filter (event, subject)"
                value={logFilter}
                onChange={(e) => setLogFilter(e.target.value)}
              />
              {logFilter && <button className="btn-ghost py-1 text-xs" onClick={() => setLogFilter('')}>x</button>}
            </div>
          </div>
          {logs.length === 0 ? (
            <EmptyState
                  icon={<BellOff size={20} />}
                  text="No alert sent yet" variant="inline" />
          ) : filteredLogs.length === 0 ? (
            <EmptyState
                  icon={<SearchX size={20} />}
                  text="No alert matches the filter" variant="inline" />
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-gray-600 border-b">
                <tr>
                  <th className="py-2">Date</th>
                  <th>Event</th>
                  <th>Subject</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {filteredLogs.map((l) => (
                  <tr key={l.id} className="border-b last:border-0">
                    <td className="py-2 font-mono text-xs whitespace-nowrap">
                      {fmt.datetime(l.created_at)}
                    </td>
                    <td className="font-mono text-xs">{l.event_type}</td>
                    <td className="text-xs">{l.subject}</td>
                    <td>
                      <span className={`text-xs px-2 py-1 rounded font-medium ${
                        l.success ? 'bg-emerald-100 text-emerald-800' : 'bg-red-100 text-red-800'
                      }`} title={l.error || ''}>
                        {l.success ? 'sent' : 'failed'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      <ConfirmHost />
    </div>
  )
}

function SmtpPanel({ cfg, form, setForm, onTest, busy }: {
  cfg: NotificationConfig
  form: NotificationConfigInput
  setForm: (f: NotificationConfigInput) => void
  onTest: () => Promise<void>
  busy: boolean
}) {
  return (
    <div className="card">
      <CardHeader title="SMTP configuration">
        <button className="btn-secondary" onClick={onTest} disabled={busy || !cfg.enabled}>
          {busy ? 'In progress...' : 'Send a test email'}
        </button>
      </CardHeader>

      {/* Enable/disable lives on the page header toggle (with a
          confirmation modal). Removed here to avoid two switches. */}

      <div className="text-xs text-gray-600 mb-3">
        TLS mode is derived from the port (587 STARTTLS, 465 SMTPS, 25 plain).
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label="SMTP server">
          <input className="input" value={form.smtp_host} placeholder="smtp.example.com"
            onChange={(e) => setForm({ ...form, smtp_host: e.target.value })} />
        </Field>
        <Field label="Port" hint="587 = STARTTLS, 465 = SMTPS, 25 = plain">
          <input type="number" className="input" value={form.smtp_port}
            onChange={(e) => {
              const p = parseInt(e.target.value) || 587
              // Auto-deduce TLS depuis le port : 25 = clair, 465/587 = TLS
              setForm({ ...form, smtp_port: p, use_tls: p === 465 || p === 587 })
            }} />
        </Field>
        <Field label="SMTP user (optional)">
          <input className="input" value={form.smtp_user || ''}
            onChange={(e) => setForm({ ...form, smtp_user: e.target.value || null })} />
        </Field>
        <Field label="SMTP password">
          <input type="password" className="input" value={form.smtp_password || ''}
            placeholder="(unchanged if empty)"
            onChange={(e) => setForm({ ...form, smtp_password: e.target.value || null })} />
        </Field>
      </div>



      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
        <Field label="Sender address">
          <input className="input" value={form.from_addr}
            onChange={(e) => setForm({ ...form, from_addr: e.target.value })} />
        </Field>
        <Field label="Recipients" hint="Comma-separated addresses">
          <input className="input" value={form.to_addrs} placeholder="admin@example.com"
            onChange={(e) => setForm({ ...form, to_addrs: e.target.value })} />
        </Field>
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

