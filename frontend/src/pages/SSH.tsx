import { useEffect, useState } from 'react'
import {
  api, type SshStatus, type SshConfig, type SshConfigInput,
  type SshAuthorizedKey,
} from '../lib/api'
import PageHeader from '../components/PageHeader'
import { KeyRound, Terminal } from 'lucide-react'
import EmptyState from '../components/EmptyState'
import FormActions from '../components/FormActions'
import { isDirty } from '../lib/dirty'
import CardHeader from '../components/CardHeader'
import { ErrorBlock, SuccessBlock } from '../components/Alerts'
import { useConfirm } from '../components/ConfirmModal'
import { ServiceStatusInline } from '../components/ServiceStatusLine'

// MurOS is a firewall appliance: the sshd drop-in is always present.
// The UI lets the operator change how sshd behaves (listen address,
// port, root login policy, auth methods, keepalive). There is no
// enable/disable switch any more: removing the drop-in would silently
// fall back to Debian defaults (port 22, password auth, root by
// password) which we do not want to surface as a one-click option.

function formFromCfg(c: SshConfig): SshConfigInput {
  return {
    port: c.port,
    // MurOS always binds sshd on every interface (0.0.0.0). Restricting
    // who can reach it is done with firewall rules, so there is no
    // per-interface selector on this page.
    listen_address: '0.0.0.0',
    permit_root_login: c.permit_root_login,
    password_authentication: c.password_authentication,
    pubkey_authentication: c.pubkey_authentication,
    max_auth_tries: c.max_auth_tries,
    client_alive_interval: c.client_alive_interval,
    client_alive_count_max: c.client_alive_count_max,
  }
}

export default function SSH() {
  const [status, setStatus] = useState<SshStatus | null>(null)
  const [cfg, setCfg] = useState<SshConfig | null>(null)
  const [form, setForm] = useState<SshConfigInput | null>(null)
  const [busy, setBusy] = useState(false)
  // Distinct from `busy`: only flips while the operator clicks the
  // service on/off toggle. Drives the small spinner shown next to the
  // toggle in the PageHeader so the spinner does not also light up
  // during a regular Apply.
  const [toggleBusy, setToggleBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const { confirm, ConfirmHost } = useConfirm()

  const reload = async () => {
    try {
      const [s, c] = await Promise.all([
        api.ssh.status(),
        api.ssh.getConfig(),
      ])
      setStatus(s); setCfg(c)
      if (!form) setForm(formFromCfg(c))
    } catch (e) { setErr((e as Error).message) }
  }
  useEffect(() => { reload() }, [])

  const apply = async () => {
    if (!form) return
    setBusy(true); setErr(null); setMsg(null); setPreview(null)
    try {
      // Enregistre puis applique en une seule action
      await api.ssh.updateConfig(form)
      const r = await api.ssh.apply({ skip_rollback: !!form.skip_rollback })
      setMsg(r.message)
      if (r.preview) setPreview(r.preview)
      // RollbackModal global gere le countdown post-apply.
      await reload()
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  // Orange dot on Apply while the form diverges from server.
  const cfgDirty = isDirty(form, cfg && formFromCfg(cfg))

  // Toggle sshd on/off entirely. Distinct from the Apply button (which
  // only reloads the daemon to pick up new drop-in settings). Disabling
  // sshd is a high-risk operation: we ask the operator to type
  // "disable ssh" so it cannot be triggered by an accidental click.
  const serviceEnabled = !!status && !status.admin_disabled
  const toggleService = async () => {
    if (!status) return
    const next = !serviceEnabled
    const ok = await confirm(next ? {
      title: 'Enable SSH service ?',
      message: 'sshd will be started now and at every boot. Connections will be accepted on the configured port and listen address.',
      confirmLabel: 'Enable SSH',
    } : {
      title: 'Disable SSH service ?',
      message: (
        <div className="space-y-2">
          <p>
            If you disable SSH now, you will lose remote shell access to
            this firewall. The web UI will keep working.
          </p>
          <p>
            Make sure you have out-of-band access (console / IPMI /
            hypervisor) before confirming, otherwise you will only be
            able to manage this appliance through the web UI.
          </p>
        </div>
      ),
      destructive: true,
      requireText: 'disable ssh',
      confirmLabel: 'Disable SSH',
    })
    if (!ok) return
    setToggleBusy(true); setErr(null); setMsg(null)
    try {
      const r = await api.ssh.toggleService(next)
      setMsg(r.message)
      await reload()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setToggleBusy(false)
    }
  }

  if (!form) return null

  const rootByPasswordWarning =
    form.permit_root_login === 'yes' && form.password_authentication

  return (
    <div>
      <PageHeader
        icon={<Terminal size={16} />}
        title="SSH access"
        description="sshd configuration for the appliance."
        status={status && (
          <ServiceStatusInline
            state={
              status.admin_disabled
                ? 'inactive'
                : (status.service_active ? 'active' : 'inactive')
            }
            version={status.version}
          />
        )}
        serviceEnabled={serviceEnabled}
        serviceToggleBusy={toggleBusy || !status}
        serviceToggleTitle={serviceEnabled
          ? 'SSH service enabled. Click to ask for confirmation before stopping sshd and disabling it at boot.'
          : 'SSH service disabled by admin. Click to start sshd and enable it at boot.'}
        onServiceEnabledChange={toggleService}
        actions={<FormActions onApply={apply} busy={busy} dirty={cfgDirty} />}
      />
      <ConfirmHost />
      <div className="px-6 py-4 space-y-6">
        {err && <ErrorBlock message={err} />}
        {msg && <SuccessBlock message={msg} onDismiss={() => setMsg(null)} />}

        {status?.admin_disabled && (
          // Product decision: the configuration form stays editable even
          // when sshd is administratively disabled. The operator can
          // prepare the next listen address, port and auth policy ahead
          // of time, then re-enable the service when ready. We surface
          // this explicitly so a Save does not feel like a no-op while
          // the daemon is stopped.
          <div className="border border-slate-200 bg-slate-50 text-slate-800 px-3 py-2 rounded text-sm">
            <span className="font-medium">SSH service is currently disabled.</span>{' '}
            You can still edit and save the configuration below. Changes
            will be written to the drop-in immediately and will take
            effect the next time SSH is enabled.
          </div>
        )}

        {status && !status.sshd_installed && (
          <div className="bg-amber-50 border border-amber-200 text-amber-900 px-3 py-3 rounded text-sm">
            <div className="flex items-start justify-between gap-3 flex-wrap">
              <div>
                <div className="font-medium">openssh-server package missing</div>
                <div className="mt-1">
                  Without openssh-server, you cannot SSH into the firewall.
                  You can install it now.
                </div>
              </div>
              <button className="btn-primary whitespace-nowrap"
                onClick={async () => {
                  setBusy(true); setErr(null); setMsg(null)
                  try { await api.ssh.install(); setMsg('openssh-server installed.'); await reload() }
                  catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
                }} disabled={busy}>
                {busy ? 'Installing...' : 'Install now'}
              </button>
            </div>
          </div>
        )}

        <div className="card">
          <CardHeader title="Server settings" />

          <div className="text-xs text-gray-600 mb-3">
            sshd listens on every interface. Restrict who can reach it with
            firewall rules (input chain), the same way you control any other
            service.
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <F label="SSH port" hint="The firewall must allow this port in input.">
              <input type="number" className="input" value={form.port}
                onChange={(e) => setForm({ ...form, port: parseInt(e.target.value) || 22 })} />
            </F>
            <F label="MaxAuthTries" hint="Authentication attempts before disconnect.">
              <input type="number" className="input" value={form.max_auth_tries}
                onChange={(e) => setForm({ ...form, max_auth_tries: parseInt(e.target.value) || 3 })} />
            </F>
            <F label="Keepalive interval (s)" hint="Dead session detection.">
              <input type="number" className="input" value={form.client_alive_interval}
                onChange={(e) => setForm({ ...form, client_alive_interval: parseInt(e.target.value) || 300 })} />
            </F>
            <F label="Keepalive count max" hint="Unanswered keepalives before disconnect.">
              <input type="number" className="input" value={form.client_alive_count_max}
                onChange={(e) => setForm({ ...form, client_alive_count_max: parseInt(e.target.value) || 2 })} />
            </F>
          </div>
        </div>

        <div className="card">
          <CardHeader title="Authentication" />

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <F label="Root login policy" hint="Controls how the root account may log in over SSH.">
              <select className="input" value={form.permit_root_login}
                onChange={(e) => setForm({ ...form, permit_root_login: e.target.value as SshConfigInput['permit_root_login'] })}>
                <option value="prohibit-password">prohibit-password (SSH key only)</option>
                <option value="yes">yes (key or password)</option>
                <option value="no">no (root cannot log in over SSH)</option>
              </select>
            </F>
            <div />
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-0.5"
                checked={form.pubkey_authentication}
                onChange={(e) => setForm({ ...form, pubkey_authentication: e.target.checked })} />
              <span>
                <span className="font-medium">Public-key authentication</span>
                <span className="block text-xs text-gray-600">Authorized keys live in <code>/root/.ssh/authorized_keys</code> (managed below).</span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-0.5"
                checked={form.password_authentication}
                onChange={(e) => setForm({ ...form, password_authentication: e.target.checked })} />
              <span>
                <span className="font-medium">Password authentication</span>
                <span className="block text-xs text-gray-600">Off by default. Turn on only if you cannot deploy SSH keys.</span>
              </span>
            </label>
          </div>

          {!form.pubkey_authentication && !form.password_authentication && (
            <div className="mt-3 border border-amber-300 bg-amber-50 rounded p-3 text-sm text-amber-900">
              Both authentication methods are off: nobody will be able to log in over SSH.
            </div>
          )}
          {rootByPasswordWarning && (
            <div className="mt-3 border border-amber-300 bg-amber-50 rounded p-3 text-sm text-amber-900">
              Root login over SSH with a password is enabled. Prefer
              <code className="mx-1">prohibit-password</code> together
              with an authorized SSH key.
            </div>
          )}

          <div className="mt-3 border border-slate-200 rounded p-3 text-sm flex items-start gap-3">
            <input type="checkbox"
              className="mt-0.5"
              checked={!!form.skip_rollback}
              onChange={(e) => setForm({ ...form, skip_rollback: e.target.checked })} />
            <div>
              <div className="font-medium">No automatic rollback</div>
              <div className="text-xs text-slate-600 mt-0.5">
                Check if you change address/port knowing you will
                lose your current SSH session. Otherwise leave unchecked:
                MurOS reverts in 10 seconds if you do not confirm.
              </div>
            </div>
          </div>

          {preview && (
            <details className="mt-4">
              <summary className="text-xs text-blue-700 cursor-pointer">View the drop-in that will be written</summary>
              <pre className="font-mono text-xs bg-slate-100 p-3 rounded mt-2 whitespace-pre-wrap">{preview}</pre>
            </details>
          )}
        </div>

        <SshKeysPanel />
      </div>
    </div>
  )
}

function SshKeysPanel() {
  const [keys, setKeys] = useState<SshAuthorizedKey[]>([])
  const [newKey, setNewKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const { confirm, ConfirmHost } = useConfirm()

  const reload = async () => {
    try { setKeys(await api.ssh.listKeys()) } catch (e) { setErr((e as Error).message) }
  }
  useEffect(() => { reload() }, [])

  const add = async () => {
    if (!newKey.trim()) return
    setBusy(true); setErr(null); setMsg(null)
    try {
      const r = await api.ssh.addKey(newKey.trim())
      setNewKey('')
      setMsg(`Key added${r.fingerprint ? ` (${r.fingerprint})` : ''}.`)
      await reload()
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  const del = async (k: SshAuthorizedKey) => {
    const ok = await confirm({
      title: 'Delete SSH key',
      message: <p>Key <span className="font-mono">{k.comment || k.fingerprint}</span> will be removed from <code>/root/.ssh/authorized_keys</code>.</p>,
      destructive: true,
      requireText: 'delete',
    })
    if (!ok) return
    setBusy(true); setErr(null); setMsg(null)
    try {
      await api.ssh.deleteKey(k.key_b64)
      setMsg('Key deleted.')
      await reload()
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <div className="card">
      <h2 className="text-lg font-semibold mb-3">Authorized SSH keys for the root account</h2>
      {err && <div className="mb-3"><ErrorBlock message={err} /></div>}
      {msg && <SuccessBlock message={msg} onDismiss={() => setMsg(null)} />}

      <div className="text-xs text-gray-600 mb-3">
        Public keys placed in <code>/root/.ssh/authorized_keys</code>. Paste
        the full line: <code className="font-mono">ssh-ed25519 AAAA... comment</code>.
        SSH and the web UI share the same <code>root</code> account.
      </div>

      <div className="space-y-2">
        <textarea className="input font-mono text-xs h-20" value={newKey}
          placeholder="ssh-ed25519 AAAAC3Nz... jerome@laptop"
          onChange={(e) => setNewKey(e.target.value)} />
        <button className="btn-primary" onClick={add} disabled={busy || !newKey.trim()}>
          {busy ? 'Adding...' : 'Add the key'}
        </button>
      </div>

      <div className="mt-4">
        {keys.length === 0 ? (
          <EmptyState
                  icon={<KeyRound size={20} />}
                  text="No authorized SSH key" hint="Add a public key to allow passwordless login (recommended)." />
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-gray-600 border-b">
              <tr>
                <th className="py-2">Comment</th>
                <th>Type</th>
                <th>SHA-256 fingerprint</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k.key_b64} className="border-b last:border-0">
                  <td className="py-2 font-medium">{k.comment || <span className="text-gray-500">-</span>}</td>
                  <td className="font-mono text-xs">{k.type}</td>
                  <td className="font-mono text-xs text-gray-700 truncate" title={k.fingerprint}>{k.fingerprint}</td>
                  <td className="text-right">
                    <button className="btn-ghost py-1 text-red-700 hover:text-red-800" onClick={() => del(k)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <ConfirmHost />
    </div>
  )
}

function F({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="text-sm font-medium mb-1">{label}</div>
      {children}
      {hint && <div className="text-xs text-gray-600 mt-1">{hint}</div>}
    </label>
  )
}
