import { useEffect, useMemo, useState, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import TableSkeleton from '../components/TableSkeleton'
import {
  api, Health, SystemInfo, Backup, NtpStatus, NtpServers,
  DnsConfig, UpdateStatus, MurosUpdateStatus, BackupRemoteConfig, SshKey,
} from '../lib/api'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'
import LoadingState from '../components/LoadingState'
import Modal from '../components/Modal'
import Toggle from '../components/Toggle'
import FormActions from '../components/FormActions'
import { ErrorBlock } from '../components/Alerts'
import { useConfirm } from '../components/ConfirmModal'
import { toast } from '../components/Toast'
import { fmt } from '../lib/format'
import { Archive, Settings } from 'lucide-react'

type TabKey = 'general' | 'backups' | 'ntp' | 'dns' | 'updates'

// Mapping URL segment <-> internal tab key. Keeps internal code stable while
// exposing clean, intent-revealing URLs that survive bookmarks and back-button.
const URL_TO_KEY: Record<string, TabKey> = {
  maintenance: 'general',
  backups: 'backups',
  time: 'ntp',
  dns: 'dns',
  updates: 'updates',
}
const KEY_TO_URL: Record<TabKey, string> = {
  general: 'maintenance',
  backups: 'backups',
  ntp: 'time',
  dns: 'dns',
  updates: 'updates',
}

export default function System() {
  const params = useParams<{ tab?: string }>()
  const nav = useNavigate()
  const tab: TabKey = (params.tab && URL_TO_KEY[params.tab]) || 'general'
  // Pas de tab dans l'URL : on canonicalise vers /system/maintenance pour
  // que le bookmark soit propre et que les diffs URL aient du sens.
  useEffect(() => {
    if (!params.tab) nav('/system/maintenance', { replace: true })
  }, [params.tab, nav])
  const setTab = (k: TabKey) => nav(`/system/${KEY_TO_URL[k]}`)
  return (
    <div>
      <PageHeader
        icon={<Settings size={16} />}
       
        title="System"
        description="Backups, sync and updates."
      />
      <div className="px-6 py-4">
        <Tabs tab={tab} onChange={setTab} />
        <div className="mt-4">
          {tab === 'general' && <GeneralTab />}
          {tab === 'backups' && <BackupsTab />}
          {tab === 'ntp' && <NtpTab />}
          {tab === 'dns' && <DnsTab />}
          {tab === 'updates' && <UpdatesTab />}
        </div>
      </div>
    </div>
  )
}

const TABS: { key: TabKey; label: string }[] = [
  { key: 'general', label: 'Maintenance' },
  { key: 'backups', label: 'Backups' },
  { key: 'ntp', label: 'Time' },
  { key: 'dns', label: 'DNS' },
  { key: 'updates', label: 'Updates' },
]

function Tabs({ tab, onChange }: { tab: TabKey; onChange: (t: TabKey) => void }) {
  return (
    <div className="flex border-b border-gray-200">
      {TABS.map((t) => {
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

function Section({ title, children, actions }: {
  title: string; children: React.ReactNode; actions?: React.ReactNode
}) {
  return (
    <section className="mb-6">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
        {actions && <div className="flex gap-2">{actions}</div>}
      </div>
      {children}
    </section>
  )
}

// Wrapper vers le helper centralise. On garde le nom local pour
// minimiser le diff sur les call-sites (1 usage dans la liste backups).
const formatBytes = (n: number): string => fmt.bytes(n)

function formatDateFR(iso: string | null | undefined): string {
  if (!iso) return 'never'
  try { return fmt.datetime(iso) } catch { return iso }
}

/* --- Onglet General --- */
function GeneralTab() {
  const [info, setInfo] = useState<SystemInfo | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  useEffect(() => {
    api.systemInfo().then(setInfo).catch(() => {})
    api.health().then(setHealth).catch(() => {})
  }, [])
  return (
    <>
      <Section title="System information">
        <div className="border border-gray-200 rounded-md">
          <Row label="API state" value={health ? health.status : 'unknown'} />
          <Row label="Version MurOS" value={health?.version || '-'} />
          <Row label="Mode" value={
            health?.apply_enabled === undefined ? '-'
            : health.apply_enabled ? 'Production (apply enabled)'
            : 'Dry-run (apply disabled)'
          } />
          <Row label="Uptime backend" value={
            health?.uptime_seconds === undefined ? '-' : fmt.duration(health.uptime_seconds)
          } />
          <Row label="Hostname" value={info?.hostname || '-'} />
          <Row label="Kernel" value={info?.kernel || '-'} />
          <Row label="Architecture" value={info?.arch || '-'} last />
        </div>
      </Section>
      <Section title="Project">
        <div className="border border-gray-200 rounded-md p-4 text-sm text-gray-800 space-y-1.5">
          <div>
            Source :{' '}
            <a href="https://github.com/murosorg/muros" target="_blank" rel="noreferrer noopener"
               className="text-blue-700 hover:underline font-mono">github.com/murosorg/muros</a>
          </div>
          <div>
            Issues :{' '}
            <a href="https://github.com/murosorg/muros/issues" target="_blank" rel="noreferrer noopener"
               className="text-blue-700 hover:underline font-mono">github.com/murosorg/muros/issues</a>
          </div>
          <div>Licence : <span className="font-mono">AGPL-3.0</span></div>
        </div>
      </Section>
      <ApplyConfirmTimeoutSetting />
      <PowerActions />
    </>
  )
}

/**
 * Apply confirmation timeout
 *
 * Controls the countdown that follows every Apply (firewall ruleset,
 * NAT, interface IP, route, VLAN). Past the countdown, if the operator
 * has not confirmed, the change is rolled back automatically. The
 * value is persisted in the system_settings table and read by the
 * unified rollback manager at register time, so a new value applies
 * to the very next Apply without a backend restart.
 */
function ApplyConfirmTimeoutSetting() {
  const [value, setValue] = useState<number | null>(null)
  const [choices, setChoices] = useState<number[]>([10, 30, 60, 120, 300])
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    api.systemSettings.get().then((data) => {
      setValue(data.apply_confirm_timeout.value)
      setChoices(data.apply_confirm_timeout.choices)
    }).catch((e) => setErr((e as Error).message))
  }, [])

  const onChange = async (next: number) => {
    setSaving(true); setErr(null)
    try {
      const r = await api.systemSettings.setApplyConfirmTimeout(next)
      setValue(r.value)
      toast.success(`Apply confirmation timeout set to ${r.value}s`)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Section title="Apply confirmation">
      {err && <div className="mb-3"><ErrorBlock message={err} /></div>}
      <div className="border border-gray-200 rounded-md p-4 space-y-3">
        <div className="text-sm text-gray-700">
          Countdown allowed before an unconfirmed Apply is rolled back
          automatically. Applies to firewall, NAT, interface, route and
          VLAN changes. A shorter value tightens the safety net at the
          cost of less time to verify connectivity from another session.
        </div>
        <div className="flex items-center gap-3 text-sm">
          <label className="text-gray-700">Timeout</label>
          <select
            className="border border-gray-300 rounded px-2 py-1 text-sm"
            value={value ?? 60}
            onChange={(e) => onChange(Number(e.target.value))}
            disabled={saving || value === null}
          >
            {choices.map((s) => (
              <option key={s} value={s}>{s} seconds</option>
            ))}
          </select>
          {saving && <span className="text-xs text-gray-500">Saving...</span>}
        </div>
      </div>
    </Section>
  )
}

function PowerActions() {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const { confirm, ConfirmHost } = useConfirm()

  const doReboot = async () => {
    const ok = await confirm({
      title: 'Reboot the firewall',
      message: 'All admin sessions and network traffic will be cut for ~1 minute. The interface will become unreachable.',
      destructive: true,
      confirmLabel: 'Reboot',
      requireText: 'REBOOT',
    })
    if (!ok) return
    setBusy(true); setErr(null)
    try {
      const r = await api.systemActions.reboot()
      toast.success(r.message + ' The interface will become unreachable.')
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  const doShutdown = async () => {
    const ok = await confirm({
      title: 'Shutdown the firewall',
      message: 'Physical or IPMI access will be required to power on. Traffic will be cut immediately.',
      destructive: true,
      confirmLabel: 'Shutdown',
      requireText: 'SHUTDOWN',
    })
    if (!ok) return
    setBusy(true); setErr(null)
    try {
      const r = await api.systemActions.shutdown()
      toast.success(r.message + ' The interface will become unreachable.')
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <Section title="Power">
      <ConfirmHost />
      {err && <div className="mb-3"><ErrorBlock message={err} /></div>}
      <div className="border border-gray-200 rounded-md p-4 space-y-3">
        <div className="text-sm text-gray-700">
          Actions sensibles : cut all admin sessions and network traffic.
          A confirmation word must be typed.
        </div>
        <div className="flex gap-2 flex-wrap">
          <button className="btn-secondary" onClick={doReboot} disabled={busy}>
            Reboot the firewall
          </button>
          <button className="btn-secondary" onClick={doShutdown} disabled={busy}>
            Shutdown the firewall
          </button>
        </div>
      </div>
    </Section>
  )
}

function Row({ label, value, last }: { label: string; value: string; last?: boolean }) {
  return (
    <div className={`flex items-center px-4 py-2.5 ${last ? '' : 'border-b border-gray-200'}`}>
      <div className="text-xs uppercase tracking-wider text-gray-700 w-48">{label}</div>
      <div className="text-sm font-mono text-gray-900">{value}</div>
    </div>
  )
}

/* --- Backups tab --- */
function BackupsTab() {
  const [list, setList] = useState<Backup[]>([])
  const [filter, setFilter] = useState('')

  // Filtre live de la liste des snapshots : grep sur nom + label. Utile
  // quand la retention a accumule des dizaines de snapshots avec des
  // noms tagges pre-upgrade-XXX, pre-restore-XXX, manual-XXX, etc.
  const filteredList = useMemo(() => {
    if (!filter.trim()) return list
    const needle = filter.toLowerCase()
    return list.filter((b) =>
      (b.name || '').toLowerCase().includes(needle) ||
      (b.label || '').toLowerCase().includes(needle)
    )
  }, [list, filter])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [label, setLabel] = useState('')
  const [working, setWorking] = useState(false)
  const [remote, setRemote] = useState<BackupRemoteConfig | null>(null)
  const [pushingName, setPushingName] = useState<string | null>(null)
  const { confirm, ConfirmHost } = useConfirm()

  const reload = useCallback(() => {
    setLoading(true)
    api.backups.list()
      .then((d) => { setList(d); setError(null) })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { reload() }, [reload])

  useEffect(() => {
    api.backupRemote.get().then(setRemote).catch(() => setRemote(null))
  }, [])

  const pushOne = async (name: string) => {
    setPushingName(name)
    try {
      const res = await api.backupRemote.push(name)
      if (res.dry_run) {
        toast.info(`Dry-run mode, nothing sent. Command : ${res.command || '(none)'}`)
      } else if (res.pushed) {
        toast.success('Snapshot sent to the remote server.')
      } else {
        toast.error(`Failed: ${res.message}`)
      }
      const r = await api.backupRemote.get()
      setRemote(r)
    } catch (e) {
      setError((e as Error).message)
    } finally { setPushingName(null) }
  }

  const create = async () => {
    setWorking(true)
    try {
      await api.backups.create(label || undefined)
      setLabel('')
      reload()
    } catch (e) {
      setError((e as Error).message)
    } finally { setWorking(false) }
  }

  const remove = async (name: string) => {
    const ok = await confirm({
      title: 'Delete the snapshot',
      message: `Snapshot ${name} will be permanently deleted.`,
      destructive: true,
      requireText: 'delete',
    })
    if (!ok) return
    try { await api.backups.remove(name); reload() } catch (e) { setError((e as Error).message) }
  }

  const restore = async (name: string) => {
    const ok = await confirm({
      title: `Restore ${name} ?`,
      message: 'This will overwrite the current SQLite DB. The API will reload after restore.',
      confirmLabel: 'Restore',
    })
    if (!ok) return
    setWorking(true)
    try {
      const res = await api.backups.restore(name)
      const msg = `Snapshot restored. ${res.db_restored ? 'Database replaced. ' : ''}Config files in ${res.extracted_to}.`
      toast.success(msg)
    } catch (e) { setError((e as Error).message) } finally { setWorking(false) }
  }

  return (
    <div>
      <ConfirmHost />
      {error && <div className="mb-4"><ErrorBlock message={error} /></div>}
      <p className="text-xs text-gray-700 mb-3">
        All-in-one snapshots: MurOS database, nftables ruleset, network config, NTP and DNS.
        Stored in <code className="font-mono">/var/lib/muros/backups</code>, retention on the last 14.
      </p>
      <Section
        title="Snapshots"
        actions={
          <>
            <input
              className="input w-48"
              placeholder="Filter (name, label)"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <input
              className="input w-48"
              placeholder="Optional label"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
            />
            <button className="btn-primary" onClick={create} disabled={working}>
              Create a snapshot
            </button>
          </>
        }
      >
        <div className="border border-gray-200 rounded-md overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-700 text-xs uppercase tracking-wider">
              <tr>
                <th className="text-left px-3 py-2">Name</th>
                <th className="text-left px-3 py-2 w-44">Created on</th>
                <th className="text-left px-3 py-2 w-40">Libelle</th>
                <th className="text-right px-3 py-2 w-24">Taille</th>
                <th className="text-right px-3 py-2 w-56"></th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <TableSkeleton rows={5} cols={5} />
              )}
              {!loading && list.length === 0 && (
                <tr><td colSpan={5}><EmptyState
                  icon={<Archive size={20} />}
                  text="No configuration snapshot" hint="Click Create a snapshot to archive the current state (MurOS DB + nft ruleset + interfaces)." /></td></tr>
              )}
              {!loading && list.length > 0 && filteredList.length === 0 && (
                <tr><td colSpan={5}><EmptyState text="No snapshot matches the filter" variant="inline" /></td></tr>
              )}
              {filteredList.map((b) => (
                <tr key={b.name} className="border-t border-gray-200">
                  <td className="px-3 py-2 font-mono text-xs">{b.name}</td>
                  <td className="px-3 py-2">{formatDateFR(b.created_at)}</td>
                  <td className="px-3 py-2">{b.label || <span className="text-gray-500">(none)</span>}</td>
                  <td className="px-3 py-2 text-right font-mono text-xs">{formatBytes(b.size_bytes)}</td>
                  <td className="px-3 py-2 text-right">
                    {remote?.enabled && (
                      <button
                        className="btn-ghost mr-1"
                        onClick={() => pushOne(b.name)}
                        disabled={pushingName === b.name}
                        title="Send this snapshot to the remote server"
                      >
                        {pushingName === b.name ? 'Sending...' : 'Send'}
                      </button>
                    )}
                    <button className="btn-ghost py-1" onClick={() => restore(b.name)} disabled={working}>Restore</button>
                    <button className="btn-ghost py-1 text-red-700 hover:text-red-800" onClick={() => remove(b.name)} disabled={working}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <BackupRemoteSection
        config={remote}
        onChange={setRemote}
        onError={setError}
      />
    </div>
  )
}

/* --- Bloc backup distant rsync --- */
function BackupRemoteSection({
  config,
  onChange,
  onError,
}: {
  config: BackupRemoteConfig | null
  onChange: (c: BackupRemoteConfig) => void
  onError: (msg: string) => void
}) {
  const [draft, setDraft] = useState<BackupRemoteConfig | null>(config)
  const [working, setWorking] = useState(false)
  const [testResult, setTestResult] = useState<string | null>(null)

  useEffect(() => { setDraft(config) }, [config])

  if (!draft) {
    return (
      <Section title="Remote backup">
        <LoadingState variant="inline" />
      </Section>
    )
  }

  const save = async () => {
    setWorking(true); setTestResult(null)
    try {
      const c = await api.backupRemote.set(draft)
      onChange(c); setDraft(c)
    } catch (e) {
      onError((e as Error).message)
    } finally { setWorking(false) }
  }

  const testIt = async () => {
    // Validation cote UI : feedback immediat plutot qu'un bouton inerte.
    if (!draft.host || !draft.user) {
      setTestResult('KO: enter the host and user before testing.')
      return
    }
    setWorking(true); setTestResult('Test in progress...')
    try {
      // On envoie les valeurs en cours du formulaire (sans avoir besoin
      // de cliquer Save d'abord). On filtre aux champs attendus
      // par BackupRemoteConfigIn cote backend.
      const payload = {
        enabled: draft.enabled,
        host: draft.host,
        user: draft.user,
        port: draft.port,
        path: draft.path,
        ssh_key_path: draft.ssh_key_path,
      }
      const r = await api.backupRemote.test(payload)
      setTestResult(`${r.ok ? 'OK' : (r.dry_run ? 'DRY-RUN' : 'KO')} : ${r.message}`)
    } catch (e) {
      setTestResult(`KO : ${(e as Error).message}`)
    } finally { setWorking(false) }
  }

  return (
    <Section
      title="Remote backup (rsync via SSH)"
      actions={
        <>
          <SshKeyManager keyPath={draft.ssh_key_path} />
          <FormActions
            onApply={save}
            busy={working}
            dirty={!!config && JSON.stringify(draft) !== JSON.stringify(config)}
            title="Save the remote backup configuration."
            extra={
              <button className="btn-secondary" onClick={testIt} disabled={working}>
                {working ? 'Test in progress...' : 'Test the connection'}
              </button>
            }
          />
        </>
      }
    >
      <p className="text-xs text-gray-700 mb-3">
        Pushes snapshots to a remote server via rsync over SSH.
        Use the "SSH key" button to generate a key pair if you don't have one yet.
        The generated public key must be copied into the <code className="font-mono">~/.ssh/authorized_keys</code> of
        the remote user.
      </p>

      <div className="border border-gray-200 rounded-md p-4 space-y-3 bg-white">
        <div className="flex items-center gap-2">
          <Toggle checked={draft.enabled}
            onChange={(v) => setDraft({ ...draft, enabled: v })} />
          <span className="text-sm font-medium text-gray-900">Enable remote send</span>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Host">
            <input
              className="input"
              placeholder="backup.example.local"
              value={draft.host}
              onChange={(e) => setDraft({ ...draft, host: e.target.value })}
            />
          </Field>
          <Field label="SSH user">
            <input
              className="input"
              placeholder="muros-backup"
              value={draft.user}
              onChange={(e) => setDraft({ ...draft, user: e.target.value })}
            />
          </Field>
          <Field label="Port SSH">
            <input
              className="input"
              type="number"
              min={1}
              max={65535}
              value={draft.port}
              onChange={(e) => setDraft({ ...draft, port: Number(e.target.value) || 22 })}
            />
          </Field>
          <Field label="Remote path">
            <input
              className="input"
              placeholder="/srv/backups/firewall-01"
              value={draft.path}
              onChange={(e) => setDraft({ ...draft, path: e.target.value })}
            />
          </Field>
          <Field label="Local SSH key" hint="default /var/lib/muros/ssh/id_ed25519">
            <input
              className="input"
              value={draft.ssh_key_path}
              onChange={(e) => setDraft({ ...draft, ssh_key_path: e.target.value })}
            />
          </Field>
        </div>

        {testResult && (
          <div className={`text-sm font-mono px-3 py-2 rounded border ${
            testResult.startsWith('OK')
              ? 'bg-emerald-50 border-emerald-200 text-emerald-800'
              : testResult.startsWith('DRY-RUN')
                ? 'bg-amber-50 border-amber-200 text-amber-800'
                : testResult.startsWith('Test in progress')
                  ? 'bg-blue-50 border-blue-200 text-blue-800'
                  : 'bg-red-50 border-red-200 text-red-800'
          }`}>
            {testResult}
          </div>
        )}

        <div className="text-xs text-gray-700 grid grid-cols-2 gap-3 pt-2 border-t border-gray-100">
          <div>
            <div className="uppercase tracking-wider text-gray-700">Last successful push</div>
            <div className="font-mono text-gray-900">{formatDateFR(draft.last_push_at) || 'never'}</div>
          </div>
          <div>
            <div className="uppercase tracking-wider text-gray-700">Last error</div>
            <div className="font-mono text-red-700">{draft.last_error || 'none'}</div>
          </div>
        </div>
      </div>
    </Section>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs uppercase tracking-wider text-gray-700 mb-1">{label}</label>
      {children}
      {hint && <div className="text-[11px] text-gray-600 mt-1">{hint}</div>}
    </div>
  )
}

/* --- Gestion paire de cles SSH du backup distant --- */
function SshKeyManager({ keyPath }: { keyPath: string }) {
  const [open, setOpen] = useState(false)
  const [key, setKey] = useState<SshKey | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { confirm, ConfirmHost } = useConfirm()

  const load = async () => {
    setLoading(true); setError(null)
    try { setKey(await api.backupRemote.getSshKey()) }
    catch (e) { setError((e as Error).message) }
    finally { setLoading(false) }
  }

  const onOpen = () => { setOpen(true); load() }

  const generate = async (force: boolean) => {
    if (force) {
      const ok = await confirm({
        title: 'Overwrite existing key?',
        message: 'Any authorization deployed on remote servers will need to be re-deployed with the new key.',
        destructive: true,
        confirmLabel: 'Regenerate',
      })
      if (!ok) return
    }
    setLoading(true); setError(null)
    try {
      const r = await api.backupRemote.generateSshKey(force)
      setKey(r)
      if (r.generated) toast.success('Pair generated. Copy the public key to the remote server.')
      else if (r.dry_run) toast.info('Dry-run: no key generated (MUROS_APPLY disabled).')
      else if (r.message) toast.info(r.message)
    } catch (e) {
      setError((e as Error).message)
    } finally { setLoading(false) }
  }

  const copy = async () => {
    if (!key?.public_key) return
    try {
      await navigator.clipboard.writeText(key.public_key)
      toast.success('Public key copied')
    } catch {
      toast.error('Auto-copy unavailable, select the text manually')
    }
  }

  return (
    <>
      <ConfirmHost />
      <button className="btn-secondary" onClick={onOpen}>SSH key</button>
      <Modal open={open} onClose={() => setOpen(false)} title="Remote backup SSH key" size="lg">
        <div className="space-y-4 text-sm">
          <p className="text-gray-700">
            MurOS generates an ed25519 key pair dedicated to remote backup. The private
            key stays on the firewall, the public key must be copied into the
            <code className="font-mono"> ~/.ssh/authorized_keys</code> file of the target user
            on the backup server.
          </p>

          <div className="border border-gray-200 rounded p-3 bg-gray-50">
            <div className="text-xs uppercase tracking-wider text-gray-700 mb-1">Local path</div>
            <div className="font-mono text-xs text-gray-900">{key?.key_path || keyPath}</div>
          </div>

          {error && <ErrorBlock message={error} />}


          {loading && <LoadingState variant="inline" />}

          {!loading && key && key.public_key && (
            <div>
              <div className="text-xs uppercase tracking-wider text-gray-700 mb-1 flex items-center justify-between">
                <span>Public key to copy on the remote server</span>
                <button className="btn-ghost text-xs" onClick={copy}>Copy</button>
              </div>
              <textarea
                readOnly
                rows={3}
                className="input font-mono text-xs w-full break-all"
                value={key.public_key}
              />
              <div className="mt-3">
                <div className="text-xs uppercase tracking-wider text-gray-700 mb-1">Command to run on the remote server</div>
                <pre className="bg-gray-900 text-gray-100 text-xs p-3 rounded font-mono overflow-x-auto whitespace-pre">{`mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo '${key.public_key}' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys`}</pre>
              </div>
            </div>
          )}

          {!loading && key && !key.public_key && (
            <div className="text-sm text-amber-800 bg-amber-50 border border-amber-200 px-3 py-2 rounded">
              No key generated yet. Click "Generate a new pair" below.
            </div>
          )}

          <div className="border-t border-gray-200 pt-3">
            <div className="text-xs uppercase tracking-wider text-gray-700 mb-2">Manual method (on the firewall)</div>
            <pre className="bg-gray-900 text-gray-100 text-xs p-3 rounded font-mono overflow-x-auto whitespace-pre">{`ssh-keygen -t ed25519 -f ${key?.key_path || keyPath} -N "" -C "muros-backup"
cat ${key?.key_path || keyPath}.pub`}</pre>
            <p className="text-xs text-gray-600 mt-2">
              If you prefer generating the key yourself (e.g. to add a passphrase),
              use these commands as root on the firewall.
            </p>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            {key?.public_key ? (
              <button className="btn-danger" onClick={() => generate(true)} disabled={loading}>
                Regenerate (overwrite)
              </button>
            ) : (
              <button className="btn-primary" onClick={() => generate(false)} disabled={loading}>
                Generate a new pair
              </button>
            )}
            <button className="btn-secondary" onClick={() => setOpen(false)}>Close</button>
          </div>
        </div>
      </Modal>
    </>
  )
}



/* --- Onglet NTP --- */
function NtpTab() {
  // Bloc minimaliste : on s'appuie sur systemd-timesyncd natif de Debian 13.
  // MurOS lit l'etat via `timedatectl show` et pose un drop-in
  // /etc/systemd/timesyncd.conf.d/muros.conf avec la liste de serveurs.
  // Pas de table de sources, pas de stratum/poll/reach. Si quelqu'un veut
  // ce detail il fait `timedatectl timesync-status` en SSH.
  const [status, setStatus] = useState<NtpStatus | null>(null)
  const [config, setConfig] = useState<NtpServers | null>(null)
  const [serversText, setServersText] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [working, setWorking] = useState(false)

  const reload = useCallback(() => {
    Promise.all([api.ntp.status(), api.ntp.servers()])
      .then(([s, c]) => {
        setStatus(s); setConfig(c); setServersText(c.servers.join(' ')); setError(null)
      })
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => { reload() }, [reload])

  const save = async () => {
    setWorking(true)
    try {
      const list = serversText.split(/\s+/).map((s) => s.trim()).filter(Boolean)
      const c = await api.ntp.setServers(list)
      setConfig(c); setServersText(c.servers.join(' '))
    } catch (e) { setError((e as Error).message) } finally { setWorking(false) }
  }

  return (
    <div>
      {error && <div className="mb-4"><ErrorBlock message={error} /></div>}
      <Section
        title="systemd-timesyncd synchronization"
        actions={<button className="btn-secondary" onClick={reload}>Refresh</button>}
      >
        {status === null && <LoadingState variant="inline" />}
        {status?.available === false && (
          <div className="text-sm text-gray-800 border border-amber-300 bg-amber-50 rounded p-3">
            systemd-timesyncd unavailable. Check that the package is installed and the service is active.
          </div>
        )}
        {status?.available && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Stat label="Synchronise" value={status.ntp_synchronized ? 'oui' : 'non'} />
            <Stat label="Service active" value={status.ntp_active ? 'yes' : 'no'} />
            <Stat label="Current source" value={status.ref_name || '(none)'} mono />
            <Stat label="Fuseau" value={status.timezone || '-'} />
          </div>
        )}
      </Section>

      <Section
        title="Serveurs NTP"
        actions={
          <FormActions
            onApply={save}
            busy={working}
            dirty={!!config && serversText.trim() !== config.servers.join(' ').trim()}
            title="Save the server list and restart systemd-timesyncd."
          />
        }
      >
        <p className="text-xs text-gray-700 mb-2">
          Space-separated server list. Written to <code className="font-mono">{config?.config_path || '/etc/systemd/timesyncd.conf.d/muros.conf'}</code>.
          The service is restarted after each save.
        </p>
        <input
          className="input w-full font-mono text-xs"
          value={serversText}
          onChange={(e) => setServersText(e.target.value)}
          placeholder="0.debian.pool.ntp.org 1.debian.pool.ntp.org 2.debian.pool.ntp.org"
        />
      </Section>
    </div>
  )
}

function Stat({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="border border-gray-200 rounded p-3 bg-white">
      <div className="text-[10px] uppercase tracking-wider text-gray-600 mb-1">{label}</div>
      <div className={`text-sm text-gray-900 ${mono ? 'font-mono' : ''}`}>{value}</div>
    </div>
  )
}

/* --- Onglet DNS --- */
function DnsTab() {
  const [cfg, setCfg] = useState<DnsConfig | null>(null)
  const [resolvers, setResolvers] = useState<string[]>([])
  const [domains, setDomains] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [working, setWorking] = useState(false)
  // When the optional DNS server (Unbound) page has flipped its "Use
  // as system resolver" toggle on, /etc/resolv.conf is owned by the
  // DNS server apply path (127.0.0.1 + fallback). Editing the
  // resolvers here would be silently overridden on the next DNS
  // server apply, which is a great way to confuse the admin. We
  // detect that mode and warn + lock the Apply button.
  const [serverManaged, setServerManaged] = useState(false)

  const reload = useCallback(() => {
    api.dns.get()
      .then((d) => { setCfg(d); setResolvers(d.resolvers); setDomains(d.search_domains); setError(null) })
      .catch((e) => setError(e.message))
    api.dnsServer.status()
      .then((s) => setServerManaged(s.system_resolver_active))
      .catch(() => setServerManaged(false))
  }, [])

  useEffect(() => { reload() }, [reload])

  const save = async () => {
    setWorking(true)
    try {
      const d = await api.dns.set({
        resolvers: resolvers.filter((r) => r.trim()),
        search_domains: domains.filter((s) => s.trim()),
      })
      setCfg(d); setResolvers(d.resolvers); setDomains(d.search_domains)
      toast.success('Resolver configuration saved')
    } catch (e) { setError((e as Error).message) } finally { setWorking(false) }
  }

  return (
    <div>
      {error && <div className="mb-4"><ErrorBlock message={error} /></div>}
      {serverManaged && (
        <div className="mb-3 text-xs text-slate-600 flex items-start gap-1.5">
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-indigo-400 mt-1.5 shrink-0"></span>
          <span>
            Resolver managed by the DNS server (Unbound). Editing is disabled here to avoid silent overrides &mdash; manage it from <strong className="text-slate-900">Services &gt; DNS server</strong>.
          </span>
        </div>
      )}
      <div className="flex items-start justify-between gap-3 mb-3">
        <p className="text-xs text-gray-700">
          File <code className="font-mono">{cfg?.config_path || '/etc/resolv.conf'}</code> written directly by MurOS, read by the native Debian glibc resolver 13.

        </p>
        {!serverManaged && (
          <FormActions
            onApply={save}
            busy={working}
            dirty={!!cfg && (
              JSON.stringify(resolvers.filter((r) => r.trim())) !== JSON.stringify(cfg.resolvers) ||
              JSON.stringify(domains.filter((s) => s.trim())) !== JSON.stringify(cfg.search_domains)
            )}
          />
        )}
      </div>

      <fieldset disabled={serverManaged} className={serverManaged ? 'opacity-60 pointer-events-none' : ''}>
      <Section title="Resolveurs DNS">
        <div className="space-y-2">
          {resolvers.map((r, i) => (
            <div key={i} className="flex gap-2">
              <input
                className="input flex-1 font-mono text-xs"
                value={r}
                onChange={(e) => setResolvers(resolvers.map((v, j) => (j === i ? e.target.value : v)))}
                placeholder="1.1.1.1 or IPv6"
              />
              <button className="btn-danger" onClick={() => setResolvers(resolvers.filter((_, j) => j !== i))}>
                Delete
              </button>
            </div>
          ))}
          <button className="btn-secondary" onClick={() => setResolvers([...resolvers, ''])}>
            Add a resolver
          </button>
        </div>
      </Section>

      <Section title="Search domains">
        <div className="space-y-2">
          {domains.map((d, i) => (
            <div key={i} className="flex gap-2">
              <input
                className="input flex-1 font-mono text-xs"
                value={d}
                onChange={(e) => setDomains(domains.map((v, j) => (j === i ? e.target.value : v)))}
                placeholder="example.local"
              />
              <button className="btn-danger" onClick={() => setDomains(domains.filter((_, j) => j !== i))}>
                Delete
              </button>
            </div>
          ))}
          <button className="btn-secondary" onClick={() => setDomains([...domains, ''])}>
            Add a domain
          </button>
        </div>
      </Section>
      </fieldset>
    </div>
  )
}

/* --- Onglet Updates --- */
function UpdatesTab() {
  const [status, setStatus] = useState<UpdateStatus | null>(null)
  const [muros, setMuros] = useState<MurosUpdateStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [installingMuros, setInstallingMuros] = useState(false)
  const [installOutput, setInstallOutput] = useState<string | null>(null)
  const [progress, setProgress] = useState<import('../lib/api').MurosUpgradeProgress | null>(null)
  const [repairing, setRepairing] = useState(false)
  const { confirm, ConfirmHost } = useConfirm()

  const [rebootInfo, setRebootInfo] = useState<{ required: boolean; packages: string[] } | null>(null)
  const [unattended, setUnattended] = useState<{ enabled: boolean; schedule: string | null; days: string[]; hour: number; minute: number; next_run: string | null; last_run: string | null; excluded_packages: string[] } | null>(null)
  const [unattendedEdit, setUnattendedEdit] = useState<{ enabled: boolean; days: string[]; hour: number; minute: number } | null>(null)
  const [unattendedSaving, setUnattendedSaving] = useState(false)

  const reload = useCallback(() => {
    api.updates.status().then(setStatus).catch((e) => setError(e.message))
    api.updates.murosStatus().then(setMuros).catch(() => setMuros(null))
    api.updates.murosProgress().then(setProgress).catch(() => setProgress(null))
    api.updates.rebootRequired().then(setRebootInfo).catch(() => setRebootInfo(null))
    api.updates.unattended().then(setUnattended).catch(() => setUnattended(null))
  }, [])

  const startEditUnattended = () => {
    if (!unattended) return
    setUnattendedEdit({
      enabled: unattended.enabled,
      days: unattended.days.length ? [...unattended.days] : ['Mon', 'Tue', 'Wed', 'Thu'],
      hour: unattended.hour,
      minute: unattended.minute,
    })
  }

  const saveUnattended = async () => {
    if (!unattendedEdit) return
    setUnattendedSaving(true); setError(null)
    try {
      const r = await api.updates.saveUnattended(unattendedEdit)
      setUnattended(r)
      setUnattendedEdit(null)
    } catch (e) { setError((e as Error).message) }
    finally { setUnattendedSaving(false) }
  }

  useEffect(() => { reload() }, [reload])

  // Poll de progression : active tant qu'un upgrade tourne ou tant que le
  // bouton "Update" est cliquable mais l'unit systemd vient juste de
  // demarrer. On poll any les 2s, et on stoppe une fois revenu en idle.
  useEffect(() => {
    if (!progress) return
    if (progress.state !== 'running' && !installingMuros) return
    const t = setInterval(() => {
      api.updates.murosProgress().then((p) => {
        setProgress(p)
        if (p.state === 'done' || p.state === 'failed') {
          // Une fois fini, recharge le status MurOS (la version a peut-etre change)
          api.updates.murosStatus().then(setMuros).catch(() => {})
        }
      }).catch(() => {})
    }, 2000)
    return () => clearInterval(t)
  }, [progress, installingMuros])

  // Detection du paquet en etat dpkg incoherent : Status contient
  // "half-configured", "half-installed", "unpacked", "triggers-pending"...
  const dpkgBroken = !!(progress?.package && progress.package.status
    && !progress.package.status.startsWith('install ok installed'))

  const repair = async () => {
    const ok = await confirm({
      title: 'Repair the muros package?',
      message: 'Runs dpkg --configure -a to finish the configuration of a package left half-installed after an interrupted update.',
      confirmLabel: 'Repair',
    })
    if (!ok) return
    setRepairing(true); setError(null)
    try {
      await api.updates.repairMuros()
      // Force un refresh apres quelques secondes
      setTimeout(reload, 2000)
    } catch (e) { setError((e as Error).message) } finally { setRepairing(false) }
  }

  const check = async () => {
    setChecking(true); setError(null)
    try {
      // Un seul appel = un seul "apt update" + relecture du candidat apt
      // (apt-cache policy). Garantit que les deux flux affichent un etat
      // coherent et un last_check_at commun.
      const r = await api.updates.checkAll()
      setStatus(r.apt)
      setMuros(r.muros)
    }
    catch (e) { setError((e as Error).message) }
    finally { setChecking(false) }
  }

  const install = async () => {
    const ok = await confirm({
      title: 'Install system updates?',
      message: 'The operation may take several minutes and some services may restart. MurOS packages are NOT touched by this button (they have their own flow).',
      confirmLabel: 'Install',
    })
    if (!ok) return
    setInstalling(true); setError(null); setInstallOutput(null)
    try {
      const r = await api.updates.install()
      setInstallOutput(r.output_tail)
      reload()
    } catch (e) { setError((e as Error).message) } finally { setInstalling(false) }
  }

  const installMurosNow = async () => {
    const ok = await confirm({
      title: `Update MurOS to ${muros?.candidate ?? 'the latest version'}?`,
      message: 'A pre-update snapshot (DB + nft ruleset) is created automatically before the operation. The apt-get runs in a detached systemd unit to survive the backend restart. The UI will reconnect automatically.',
      confirmLabel: 'Update',
    })
    if (!ok) return
    setInstallingMuros(true); setError(null); setInstallOutput(null)
    try {
      const r = await api.updates.installMuros()
      setInstallOutput(r.output_tail)
      // Force un premier poll de progression tout de suite
      api.updates.murosProgress().then(setProgress).catch(() => {})
      reload()
    } catch (e) { setError((e as Error).message) } finally { setInstallingMuros(false) }
  }

  if (!status) return <LoadingState variant="inline" />

  if (!status.apt_available) {
    return (
      <div className="border border-gray-200 bg-white rounded p-4 text-sm text-gray-800">
        <strong>apt is not available</strong> on this system. Update management is disabled.
      </div>
    )
  }

  // Source de verite unique pour le timestamp "derniere verification".
  // Les deux flux (apt et muros) le partagent : checkAll() les met a jour
  // ensemble, donc afficher le timestamp apt suffit a representer les deux.
  const lastCheckLabel = status.last_check_at
    ? `Last check : ${formatDateFR(status.last_check_at)}`
    : 'Not checked yet'

  return (
    <div>
      <ConfirmHost />
      {error && <div className="mb-4"><ErrorBlock message={error} /></div>}

      {rebootInfo?.required && (
        <div className="mb-3 border border-amber-300 bg-amber-50 rounded p-3 text-sm flex items-start gap-3">
          <div className="flex-1">
            <div className="font-medium text-amber-900">Reboot required</div>
            <div className="text-amber-900 text-xs mt-0.5">
              One or more recent upgrades need a reboot to take effect{rebootInfo.packages.length > 0 ? ` (${rebootInfo.packages.slice(0, 3).join(', ')}${rebootInfo.packages.length > 3 ? `, +${rebootInfo.packages.length - 3} more` : ''})` : ''}. The kernel keeps running the previous version until then. Pick a maintenance window from the Reboot tab.
            </div>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
        <p className="text-xs text-gray-700">
          MurOS is upgraded manually from the section below. Debian packages are applied automatically on the schedule shown further down. The check refreshes both streams in one go.
        </p>
        <div className="flex items-center gap-3 shrink-0">
          <span className="text-xs text-gray-600">{lastCheckLabel}</span>
          <button className="btn-secondary" onClick={check} disabled={checking}>
            {checking ? 'Checking...' : 'Check now'}
          </button>
        </div>
      </div>

      <Section
        title="MurOS Updates"
        actions={
          muros?.upgrade_available && (
            <button className="btn-primary" onClick={installMurosNow} disabled={installingMuros}>
              {installingMuros ? 'Updating...' : `MurOS Updates (${muros.candidate})`}
            </button>
          )
        }
      >
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          <Stat label="Installed version" value={muros?.installed ?? '-'} mono />
          {/* When the installed version is strictly newer than the
              latest published release (typical right after tagging a
              new rc while CI is still building it), the backend sets
              `upgrade_available=false` but the raw candidate would
              still display an older version, which looks like a
              downgrade prompt. Show "Up to date" instead so the
              operator does not second-guess the build. */}
          <Stat
            label="Available version"
            value={
              muros?.candidate
                ? (muros.upgrade_available ? muros.candidate : 'Up to date')
                : '-'
            }
            mono={!!muros?.candidate && muros.upgrade_available}
          />
          <Stat label="Source" value={muros?.candidate ? 'apt.muros.org' : 'none'} />
        </div>

        {muros?.upgrade_available && muros.candidate && (
          <div className="mt-3 text-xs">
            <a
              href={`https://github.com/murosorg/muros/releases/tag/v${muros.candidate}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-700 hover:underline"
            >Show changelog (v{muros.installed || '?'} → v{muros.candidate}) on GitHub</a>
          </div>
        )}

        {dpkgBroken && (
          <div className="mt-3 border border-red-300 bg-red-50 rounded p-3 text-sm">
            <div className="font-medium text-red-900 mb-1">muros package in inconsistent state</div>
            <div className="text-red-800 text-xs font-mono mb-2">
              {progress?.package?.status} (version {progress?.package?.version || 'n/a'})
            </div>
            <p className="text-red-900 text-xs mb-2">
              This happens when an update was interrupted (the backend restarted mid-way).
              The repair runs <code className="font-mono">dpkg --configure -a</code> in a
              detached systemd unit.
            </p>
            <button className="btn-secondary" onClick={repair} disabled={repairing}>
              {repairing ? 'Repairing...' : 'Repair dpkg'}
            </button>
          </div>
        )}

        {progress && progress.state === 'running' && (
          <div className="mt-3 border border-amber-300 bg-amber-50 rounded p-3 text-sm">
            <div className="font-medium text-amber-900 mb-1">Update in progress</div>
            <div className="text-amber-900 text-xs space-y-1">
              <p className="m-0">
                The apt-get runs in <code className="font-mono">muros-self-upgrade.service</code>.
                The backend will restart at the end of postinst, this admin interface
                will be unreachable for 2 to 5 seconds.
              </p>
              <p className="m-0">
                <span className="font-medium">Traffic forwarding is not affected.</span>{' '}
                Firewall rules, NAT, DHCP, DNS, WireGuard and IPsec tunnels keep
                serving users while the update runs. Only this admin web UI and
                its API are momentarily offline.
              </p>
            </div>
          </div>
        )}

        {progress && progress.state === 'failed' && (
          <div className="mt-3 border border-red-300 bg-red-50 rounded p-3 text-sm">
            <div className="font-medium text-red-900 mb-1">The last update failed</div>
            <div className="text-red-900 text-xs font-mono">{progress.detail || ''}</div>
          </div>
        )}

        {progress && progress.log_tail && (
          <details className="mt-3 text-sm">
            <summary className="cursor-pointer text-gray-800 hover:text-gray-900">
              Last MurOS update log
            </summary>
            <pre className="mt-2 text-xs whitespace-pre-wrap font-mono text-gray-800 bg-gray-50 border border-gray-200 rounded p-3 max-h-64 overflow-auto">
              {progress.log_tail}
            </pre>
          </details>
        )}

        {!muros?.installed && (
          <div className="text-sm text-gray-700 mt-2">
            The MurOS package is not installed via dpkg on this machine. This flow
            only activates after installing from apt.muros.org or deployment of
            the ISO appliance.
          </div>
        )}
        {muros?.installed && !muros.upgrade_available && muros.candidate && (
          <div className="text-sm text-green-700 mt-2">MurOS is up to date.</div>
        )}
        {muros?.upgrade_available && muros.release_notes && (
          <details className="mt-3 text-sm">
            <summary className="cursor-pointer text-gray-800 hover:text-gray-900">
              Changelog version {muros.candidate}
              {muros.release_published_at && (
                <span className="text-gray-700"> ({formatDateFR(muros.release_published_at)})</span>
              )}
            </summary>
            <pre className="mt-2 text-xs whitespace-pre-wrap font-mono text-gray-800 bg-gray-50 border border-gray-200 rounded p-3">
              {muros.release_notes}
            </pre>
          </details>
        )}
      </Section>

      <Section
        title="System updates (Debian)"
        actions={
          status.packages_count > 0 && (
            <button className="btn-primary" onClick={install} disabled={installing}>
              {installing ? 'Installing...' : `Install ${status.packages_count} package(s)`}
            </button>
          )
        }
      >
        {unattended && (
          <div className="mb-3 border border-gray-200 bg-gray-50 rounded p-3 text-sm">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div className="flex items-center gap-2">
                <span className={`inline-block w-2 h-2 rounded-full ${unattended.enabled ? 'bg-emerald-500' : 'bg-gray-300'}`}></span>
                <span className="font-medium text-gray-800">Automatic upgrades</span>
                <span className="text-xs text-gray-500">{unattended.enabled ? 'enabled' : 'disabled'}</span>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-xs text-gray-600">
                  {unattended.schedule ? <>Runs <span className="font-mono">{unattended.schedule}</span> (local)</> : <>Default Debian schedule</>}
                </span>
                {!unattendedEdit && (
                  <button className="btn-secondary text-xs py-1 px-2" onClick={startEditUnattended}>Edit</button>
                )}
              </div>
            </div>
            <div className="text-xs text-gray-600 mt-1">
              Security and updates pockets are applied automatically. The muros package is excluded and upgraded only from the section above. No automatic reboot: a banner appears at the top of this page when one is needed.
            </div>

            {unattendedEdit && (
              <div className="mt-3 pt-3 border-t border-gray-200 space-y-3">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={unattendedEdit.enabled}
                    onChange={(e) => setUnattendedEdit({ ...unattendedEdit, enabled: e.target.checked })}
                  />
                  <span>Enable automatic Debian upgrades</span>
                </label>

                <div>
                  <div className="text-xs text-gray-700 mb-1">Days of the week</div>
                  <div className="flex flex-wrap gap-1">
                    {(['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const).map((d) => {
                      const active = unattendedEdit.days.includes(d)
                      return (
                        <button
                          key={d}
                          type="button"
                          className={`px-2 py-1 text-xs rounded border ${active ? 'bg-gray-900 text-white border-gray-900' : 'bg-white text-gray-700 border-gray-300'}`}
                          onClick={() => {
                            const days = active
                              ? unattendedEdit.days.filter((x) => x !== d)
                              : [...unattendedEdit.days, d]
                            setUnattendedEdit({ ...unattendedEdit, days })
                          }}
                        >
                          {d}
                        </button>
                      )
                    })}
                  </div>
                  <div className="text-xs text-gray-500 mt-1">
                    Default: Mon to Thu at 03:00. On an HA pair, pick different days on each peer if you want to guarantee a 24h offset.
                  </div>
                </div>

                <div className="flex items-center gap-2 text-sm">
                  <span className="text-xs text-gray-700">Time</span>
                  <input
                    type="number" min={0} max={23}
                    className="w-16 border border-gray-300 rounded px-2 py-1 text-sm"
                    value={unattendedEdit.hour}
                    onChange={(e) => setUnattendedEdit({ ...unattendedEdit, hour: Math.max(0, Math.min(23, parseInt(e.target.value || '0', 10))) })}
                  />
                  <span className="text-gray-500">:</span>
                  <input
                    type="number" min={0} max={59}
                    className="w-16 border border-gray-300 rounded px-2 py-1 text-sm"
                    value={unattendedEdit.minute}
                    onChange={(e) => setUnattendedEdit({ ...unattendedEdit, minute: Math.max(0, Math.min(59, parseInt(e.target.value || '0', 10))) })}
                  />
                  <span className="text-xs text-gray-500">local time, with 1h random jitter</span>
                </div>

                <div className="flex justify-end gap-2">
                  <button className="btn-secondary" onClick={() => setUnattendedEdit(null)} disabled={unattendedSaving}>Cancel</button>
                  <button className="btn-primary" onClick={saveUnattended} disabled={unattendedSaving || unattendedEdit.days.length === 0}>
                    {unattendedSaving ? 'Saving...' : 'Save'}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-3">
          <Stat label="Last check" value={formatDateFR(status.last_check_at)} />
          <Stat label="Pending packages" value={String(status.packages_count)} mono />
        </div>
        {status.last_check_at && status.packages_count === 0 && (
          <div className="text-sm text-green-700">System is up to date.</div>
        )}
      </Section>

      {status.packages_count > 0 && (
        <Section title="Pending packages">
          <div className="border border-gray-200 rounded-md overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-700 text-xs uppercase tracking-wider">
                <tr>
                  <th className="text-left px-3 py-2">Packet</th>
                  <th className="text-left px-3 py-2 w-44">Version actuelle</th>
                  <th className="text-left px-3 py-2 w-44">New version</th>
                </tr>
              </thead>
              <tbody>
                {status.packages.map((p) => (
                  <tr key={p.name} className="border-t border-gray-200">
                    <td className="px-3 py-2 font-mono text-xs">{p.name}</td>
                    <td className="px-3 py-2 font-mono text-xs text-gray-700">{p.current_version}</td>
                    <td className="px-3 py-2 font-mono text-xs text-gray-900">{p.new_version}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      {installOutput && (
        <Section title="Output of the last installation">
          <pre className="text-xs font-mono bg-gray-50 border border-gray-200 rounded p-3 overflow-auto max-h-64">{installOutput}</pre>
        </Section>
      )}
    </div>
  )
}



