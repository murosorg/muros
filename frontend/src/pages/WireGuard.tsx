import { useEffect, useState } from 'react'
import {
  api,
  type WireGuardStatus,
  type WireGuardConfig,
  type WireGuardConfigInput,
  type WireGuardPeer,
  type WireGuardPeerInput,
  type WireGuardPeerExport,
} from '../lib/api'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'
import Toggle from '../components/Toggle'
import { useConfirm } from '../components/ConfirmModal'
import Modal from '../components/Modal'
import ConfirmModal from '../components/ConfirmModal'
import ApplyServiceButton from '../components/ApplyServiceButton'
import { isDirty } from '../lib/dirty'
import CardHeader from '../components/CardHeader'
import { ErrorBlock } from '../components/Alerts'
import { toast } from '../components/Toast'
import { ServiceStatusInline, type ServiceState } from '../components/ServiceStatusLine'
import { Lock, Users } from 'lucide-react'
import { useNavigate, useParams } from 'react-router-dom'

// Sub-routes: /vpn/wireguard/<tab>. Splitting the page in two tabs
// keeps the server tunnel configuration (keys, port, public endpoint)
// physically separate from the per-client peer list and avoids one
// very tall scroll. Bookmarks survive and the browser back button
// works between tabs.
type WgTab = 'server' | 'peers'

const WG_TABS: { key: WgTab; label: string }[] = [
  { key: 'server', label: 'Server' },
  { key: 'peers', label: 'Peers' },
]

function WgTabs({ tab, onChange }: { tab: WgTab; onChange: (t: WgTab) => void }) {
  return (
    <div className="flex border-b border-gray-200">
      {WG_TABS.map((t) => {
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

export default function WireGuard() {
  const params = useParams<{ tab?: string }>()
  const nav = useNavigate()
  const validTabs: WgTab[] = ['server', 'peers']
  const tab: WgTab = (params.tab && (validTabs as string[]).includes(params.tab))
    ? (params.tab as WgTab)
    : 'server'
  // Canonicalize a bare /vpn/wireguard to /vpn/wireguard/server so the
  // URL always reflects the active tab.
  useEffect(() => {
    if (!params.tab || !(validTabs as string[]).includes(params.tab)) {
      nav('/vpn/wireguard/server', { replace: true })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.tab])
  const setTab = (k: WgTab) => nav(`/vpn/wireguard/${k}`)
  const [status, setStatus] = useState<WireGuardStatus | null>(null)
  const [cfg, setCfg] = useState<WireGuardConfig | null>(null)
  const [cfgForm, setCfgForm] = useState<WireGuardConfigInput | null>(null)
  const [peers, setPeers] = useState<WireGuardPeer[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { confirm, ConfirmHost } = useConfirm()

  const [editingPeer, setEditingPeer] = useState<WireGuardPeer | null>(null)
  const [deletingPeer, setDeletingPeer] = useState<WireGuardPeer | null>(null)
  const [creatingPeer, setCreatingPeer] = useState(false)
  const [exportedPeer, setExportedPeer] = useState<{ peer: WireGuardPeer; data: WireGuardPeerExport } | null>(null)

  const reload = async () => {
    try {
      const [s, c, p] = await Promise.all([
        api.wireguard.status(),
        api.wireguard.getConfig(),
        api.wireguard.listPeers(),
      ])
      setStatus(s); setCfg(c); setPeers(p)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  useEffect(() => { reload() }, [])
  useEffect(() => {
    if (cfg) {
      setCfgForm({
        enabled: cfg.enabled,
        interface_name: cfg.interface_name,
        address_cidr: cfg.address_cidr,
        listen_port: cfg.listen_port,
        private_key: cfg.private_key,
        public_key: cfg.public_key,
        mtu: cfg.mtu,
        public_endpoint: cfg.public_endpoint || '',
      })
    }
  }, [cfg])
  useEffect(() => {
    const id = setInterval(() => {
      api.wireguard.status().then(setStatus).catch(() => {})
    }, 5000)
    return () => clearInterval(id)
  }, [])

  // Orange dot on Apply while the server form diverges from cfg.
  const cfgDirty = isDirty(cfgForm, cfg && {
    enabled: cfg.enabled,
    interface_name: cfg.interface_name,
    address_cidr: cfg.address_cidr,
    listen_port: cfg.listen_port,
    private_key: cfg.private_key,
    public_key: cfg.public_key,
    mtu: cfg.mtu,
    public_endpoint: cfg.public_endpoint || '',
  })

  const saveConfig = async (data: WireGuardConfigInput) => {
    setBusy(true); setError(null)
    try {
      await api.wireguard.updateConfig(data)
      const r = await api.wireguard.apply()
      toast.success(r.message)
      await reload()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  // Page-header quick toggle : flips the persisted enabled flag and
  // applies immediately. Independent from the Server tab form so the
  // operator can pause WireGuard without touching pending edits.
  const toggleService = async () => {
    if (!cfg) return
    const next = !cfg.enabled
    const ok = await confirm(next ? {
      title: 'Enable WireGuard ?',
      message: `Interface ${cfg.interface_name} will be brought up now and at every boot. Listed peers may start exchanging traffic immediately.`,
      confirmLabel: 'Enable',
    } : {
      title: 'Disable WireGuard ?',
      message: `Interface ${cfg.interface_name} will be brought down immediately. All peer tunnels will drop and the interface will not come back at boot until you re-enable it.`,
      confirmLabel: 'Disable',
      destructive: true,
    })
    if (!ok) return
    setBusy(true); setError(null)
    try {
      await api.wireguard.updateConfig({
        enabled: next,
        interface_name: cfg.interface_name,
        address_cidr: cfg.address_cidr,
        listen_port: cfg.listen_port,
        private_key: cfg.private_key,
        public_key: cfg.public_key,
        mtu: cfg.mtu,
        public_endpoint: cfg.public_endpoint || '',
      })
      const r = await api.wireguard.apply()
      toast.success(next ? `WireGuard enabled. ${r.message}` : 'WireGuard disabled and interface brought down.')
      await reload()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  // Page-level Apply, always available top-right regardless of the
  // current tab. Re-pushes the saved config to wg-quick (idempotent
  // when nothing changed, restarts the tunnel otherwise). Dirty dot
  // lights up only while the server-tab form has unsaved edits.
  const installPkgs = async () => {
    setBusy(true); setError(null)
    try {
      const r = await api.wireguard.install()
      toast.success(r.newly_installed.length > 0
        ? `Installed packages : ${r.newly_installed.join(', ')}.`
        : r.installed ? 'Packages already installed.' : (r.output_tail || 'Dry-run.'))
      await reload()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <div>
      <PageHeader
        icon={<Lock size={16} />}
        title="WireGuard"
        description="WireGuard VPN tunnels."
        status={status && (
          <ServiceStatusInline
            state={(status.service_state ?? (status.service_active ? 'active' : 'inactive')) as ServiceState}
            version={status.version}
          />
        )}
        serviceEnabled={!!cfg?.enabled}
        serviceToggleBusy={busy || !status?.installed}
        serviceToggleTitle={cfg?.enabled
          ? 'WireGuard enabled. Click to bring the interface down and disable it at boot.'
          : 'WireGuard disabled. Click to bring the interface up and enable it at boot.'}
        onServiceEnabledChange={toggleService}
        actions={
          <ApplyServiceButton
            service="wireguard"
            pendingTooltip="Reload wg-quick to apply the saved configuration."
            onApplied={() => { void reload(); toast.success('WireGuard reloaded.') }}
            onError={setError}
            disabled={!status?.installed}
            formDirty={tab === 'server' && cfgDirty}
          />
        }
      />

      <div className="px-6 pt-3">
        <WgTabs tab={tab} onChange={setTab} />
      </div>

      <div className="px-6 py-4 space-y-6">
        {error && <ErrorBlock message={error} onDismiss={() => setError(null)} />}

        {tab === 'server' && status && !status.installed && (
          <div className="bg-amber-50 border border-amber-200 text-amber-900 px-3 py-3 rounded text-sm">
            <div className="flex items-start justify-between gap-3 flex-wrap">
              <div>
                <div className="font-medium">Missing packages on this node</div>
                <div className="mt-1">To install: <code>wireguard wireguard-tools</code>.</div>
              </div>
              <button className="btn-primary whitespace-nowrap" onClick={installPkgs} disabled={busy}>
                {busy ? 'Installing...' : 'Install now'}
              </button>
            </div>
          </div>
        )}

        {tab === 'server' && status && status.interfaces.length > 0 && (
          <div className="card">
            <CardHeader title="Active interfaces" />
            <table className="w-full text-sm">
              <thead className="text-left text-gray-600 border-b">
                <tr><th className="py-2">Name</th><th>Port</th><th>Peers</th></tr>
              </thead>
              <tbody>
                {status.interfaces.map((iface) => (
                  <tr key={iface.name} className="border-b last:border-0">
                    <td className="py-2 font-mono">{iface.name}</td>
                    <td className="font-mono">{iface.listen_port ?? 'n/a'}</td>
                    <td>{iface.peers}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {tab === 'server' && cfgForm && (
          <ConfigPanel
            form={cfgForm}
            setForm={setCfgForm}
            dirty={cfgDirty}
            busy={busy}
            onSave={() => saveConfig(cfgForm)}
          />
        )}

        {tab === 'peers' && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">Peers</h2>
            <button className="btn-primary" onClick={() => setCreatingPeer(true)} disabled={!cfg}>
              New peer
            </button>
          </div>
          {peers.length === 0 ? (
            <EmptyState
              icon={<Users size={20} />}
              text="No WireGuard peer"
              hint="Give a name, download the .conf or scan the QR code on the client."
              action={<button className="btn-primary" onClick={() => setCreatingPeer(true)} disabled={!cfg}>New peer</button>}
            />
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-gray-600 border-b">
                <tr>
                  <th className="py-2">Name</th>
                  <th>Tunnel IP</th>
                  <th>Client routes</th>
                  <th>Endpoint</th>
                  <th>State</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {peers.map((p) => (
                  <tr key={p.id} className="border-b last:border-0">
                    <td className="py-2">
                      <div className="font-medium">{p.name}</div>
                      {p.description && <div className="text-xs text-gray-600">{p.description}</div>}
                    </td>
                    <td className="font-mono text-xs">{p.allowed_ips}</td>
                    <td className="font-mono text-xs">{p.client_allowed_ips || '0.0.0.0/0, ::/0'}</td>
                    <td className="font-mono text-xs">{p.endpoint || '-'}</td>
                    <td>
                      <span className={`text-xs px-2 py-1 rounded border ${
                        p.enabled ? 'bg-emerald-50 border-emerald-300 text-emerald-800' :
                                    'bg-slate-50 border-slate-300 text-slate-600'
                      }`}>{p.enabled ? 'enabled' : 'disabled'}</span>
                    </td>
                    <td className="text-right space-x-2">
                      <button className="btn-secondary text-xs" onClick={async () => {
                        try {
                          const data = await api.wireguard.exportPeer(p.id)
                          setExportedPeer({ peer: p, data })
                        } catch (e) { setError((e as Error).message) }
                      }}>Export</button>
                      <button className="btn-ghost py-1" onClick={() => setEditingPeer(p)}>Edit</button>
                      <button className="btn-ghost py-1 text-red-700 hover:text-red-800" onClick={() => setDeletingPeer(p)}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        )}
      </div>

      {(creatingPeer || editingPeer) && (
        <PeerModal
          peer={editingPeer}
          cfg={cfg}
          peers={peers}
          onClose={() => { setCreatingPeer(false); setEditingPeer(null) }}
          onSave={async (data, generatedPriv) => {
            try {
              let createdPeer: WireGuardPeer | null = null
              if (editingPeer) {
                await api.wireguard.updatePeer(editingPeer.id, data)
              } else {
                createdPeer = await api.wireguard.createPeer(data)
              }
              setCreatingPeer(false); setEditingPeer(null)
              // Auto apply to push into wg0
              const r = await api.wireguard.apply()
              toast.success(r.message)
              await reload()
              // If a fresh keypair was generated in the modal, immediately
              // surface the ready-to-paste client config with the private
              // key inlined (it is not stored server-side, so this is the
              // only chance to display it).
              if (createdPeer && generatedPriv) {
                try {
                  const exp = await api.wireguard.exportPeer(createdPeer.id, generatedPriv)
                  setExportedPeer({ peer: createdPeer, data: exp })
                } catch (e) { setError((e as Error).message) }
              }
            } catch (e) { setError((e as Error).message) }
          }}
        />
      )}

      <ConfirmModal
        open={!!deletingPeer}
        title="Delete WireGuard peer"
        destructive
        confirmLabel="Delete peer"
        requireText={deletingPeer?.name}
        onCancel={() => setDeletingPeer(null)}
        onConfirm={async () => {
          if (!deletingPeer) return
          try { await api.wireguard.deletePeer(deletingPeer.id); await reload() }
          catch (e) { setError((e as Error).message) }
          setDeletingPeer(null)
        }}
        message={deletingPeer ? (
          <div className="space-y-2 text-sm text-gray-800">
            <p>The peer <span className="font-mono">{deletingPeer.name}</span> will be removed from the
            WireGuard server config. Its keypair is destroyed, the corresponding client config can no
            longer authenticate.</p>
            <p className="text-xs text-gray-700">Type <code className="font-mono font-semibold">{deletingPeer.name}</code> below to confirm.</p>
          </div>
        ) : null}
      />

      {exportedPeer && (
        <Modal open={true} size="lg" onClose={() => setExportedPeer(null)} title={`Client config : ${exportedPeer.peer.name}`}>
          <div className="space-y-3">
            <div className="text-sm text-gray-600">
              Configuration file to import into the WireGuard client
              (phone, laptop).
              {exportedPeer.data.config_text.includes('<PASTE') || exportedPeer.data.config_text.includes('<FIREWALL-PUBLIC-IP')
                ? ' Replace the placeholders between angle brackets.'
                : ' The private key is shown only once; copy or download it now.'}
            </div>
            <textarea
              readOnly
              className="w-full font-mono text-xs border rounded p-2 h-48"
              value={exportedPeer.data.config_text}
            />
            <div className="flex gap-2">
              <button className="btn-secondary text-xs" onClick={async () => {
                try {
                  await navigator.clipboard.writeText(exportedPeer.data.config_text)
                  toast.success('Configuration copied')
                } catch {
                  toast.error('Auto-copy unavailable')
                }
              }}>Copy</button>
              <a
                className="btn-secondary text-xs"
                href={`data:text/plain;charset=utf-8,${encodeURIComponent(exportedPeer.data.config_text)}`}
                download={`wg-${exportedPeer.peer.name}.conf`}
              >Download .conf</a>
            </div>
            {exportedPeer.data.qr_svg && (
              <div>
                <div className="text-sm font-medium mb-2">QR code (scan from the WireGuard mobile app)</div>
                <div className="border rounded p-3 bg-white inline-block"
                  dangerouslySetInnerHTML={{ __html: exportedPeer.data.qr_svg }} />
              </div>
            )}
          </div>
        </Modal>
      )}
      <ConfirmHost />
    </div>
  )
}

function ConfigPanel({ form, setForm, dirty, busy, onSave }: {
  form: WireGuardConfigInput
  setForm: (f: WireGuardConfigInput) => void
  dirty: boolean
  busy: boolean
  onSave: () => void
}) {
  // The page lands with sensible defaults already filled in by the
  // backend: keypair generated, tunnel subnet on 10.10.0.0/24, port
  // 51820. The operator only has to type the public endpoint and turn
  // the toggle on. Everything else stays out of the way.
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [keyVisible, setKeyVisible] = useState(false)
  const [copied, setCopied] = useState(false)

  const rotateKeys = async () => {
    if (form.private_key && !confirm(
      'Rotate the server keypair? Every existing peer will need to receive the new public key.'
    )) return
    const kp = await api.wireguard.generateKeypair()
    setForm({ ...form, private_key: kp.private_key, public_key: kp.public_key })
  }

  const copyPubkey = async () => {
    try {
      await navigator.clipboard.writeText(form.public_key)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch { /* ignore */ }
  }

  return (
    <div className="card">
      <div className="flex items-start justify-between gap-3 mb-3">
        <h2 className="text-lg font-semibold">Tunnel</h2>
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

      <div className="space-y-3">
        <div className="flex items-center gap-3 text-sm">
          {/* Enable/disable lives on the page header toggle (with a
              confirmation modal). Removed here to avoid two switches. */}
          <span className="text-xs text-gray-500 font-mono">
            {form.interface_name} · {form.address_cidr || '10.10.0.1/24'} · udp/{form.listen_port}
          </span>
        </div>

        <label className="block">
          <div className="text-sm font-medium mb-1">Public endpoint</div>
          <input
            className="input font-mono text-sm"
            value={form.public_endpoint}
            placeholder="vpn.example.com or 203.0.113.10"
            onChange={(e) => setForm({ ...form, public_endpoint: e.target.value })}
          />
          <div className="text-xs text-gray-600 mt-1">
            Hostname or IP clients use to reach this firewall. Inlined in every exported peer config.
          </div>
        </label>

      </div>

      {showAdvanced && (
        <div className="mt-4 pt-4 border-t border-gray-200 space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <Field label="Interface">
              <input className="input font-mono text-sm" value={form.interface_name}
                onChange={(e) => setForm({ ...form, interface_name: e.target.value })} />
            </Field>
            <Field label="Tunnel subnet">
              <input className="input font-mono text-sm" value={form.address_cidr}
                placeholder="10.10.0.1/24"
                onChange={(e) => setForm({ ...form, address_cidr: e.target.value })} />
            </Field>
            <Field label="UDP port">
              <input type="number" className="input font-mono text-sm" value={form.listen_port}
                onChange={(e) => setForm({ ...form, listen_port: parseInt(e.target.value) || 51820 })} />
            </Field>
            <Field label="MTU">
              <input type="number" className="input font-mono text-sm" value={form.mtu ?? ''}
                placeholder="auto"
                onChange={(e) => setForm({ ...form, mtu: e.target.value ? parseInt(e.target.value) : null })} />
            </Field>
          </div>

          <div>
            <div className="text-sm font-medium mb-1">Server public key</div>
            <div className="flex items-center gap-2">
              <code className="flex-1 font-mono text-xs bg-gray-50 px-2 py-1.5 rounded border border-gray-200 break-all">
                {form.public_key || '(saving will generate one)'}
              </code>
              <button
                type="button"
                className="btn-secondary text-xs whitespace-nowrap"
                onClick={copyPubkey}
                disabled={!form.public_key}
              >
                {copied ? 'Copied' : 'Copy'}
              </button>
            </div>
          </div>

          <div>
            <div className="flex items-center justify-between mb-1">
              <div className="text-sm font-medium">Server private key</div>
              <div className="flex items-center gap-3 text-xs">
                <button
                  type="button"
                  className="text-gray-700 hover:text-gray-900 underline"
                  onClick={() => setKeyVisible((v) => !v)}
                >
                  {keyVisible ? 'Hide' : 'Show'}
                </button>
                <button type="button" className="btn-ghost py-1" onClick={rotateKeys}>
                  Rotate
                </button>
              </div>
            </div>
            <input
              className="input font-mono text-xs"
              type={keyVisible ? 'text' : 'password'}
              autoComplete="off"
              value={form.private_key}
              onChange={(e) => setForm({ ...form, private_key: e.target.value })}
              placeholder="Base64 private key (44 chars)"
            />
          </div>
        </div>
      )}

      <div className="mt-3 text-right">
        <button
          type="button"
          className="text-xs text-gray-600 hover:text-gray-900 underline"
          onClick={() => setShowAdvanced((v) => !v)}
        >
          {showAdvanced ? 'Hide advanced settings' : 'Advanced settings'}
        </button>
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

function suggestNextPeerIp(cfg: WireGuardConfig | null, peers: WireGuardPeer[]): string {
  // Pick the next free /32 inside cfg.address_cidr.
  if (!cfg || !cfg.address_cidr) return ''
  const m = cfg.address_cidr.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)\/(\d+)$/)
  if (!m) return ''
  const a = +m[1], b = +m[2], c = +m[3], d = +m[4]
  const used = new Set<number>([d])
  for (const p of peers) {
    for (const part of (p.allowed_ips || '').split(',')) {
      const mm = part.trim().match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)\/32$/)
      if (mm && +mm[1] === a && +mm[2] === b && +mm[3] === c) used.add(+mm[4])
    }
  }
  for (let i = 2; i < 255; i++) if (!used.has(i)) return `${a}.${b}.${c}.${i}/32`
  return ''
}

function PeerModal({ peer, cfg, peers, onClose, onSave }: {
  peer: WireGuardPeer | null
  cfg: WireGuardConfig | null
  peers: WireGuardPeer[]
  onClose: () => void
  onSave: (data: WireGuardPeerInput, generatedPriv?: string | null) => Promise<void>
}) {
  const [form, setForm] = useState<WireGuardPeerInput>(peer ? {
    name: peer.name, public_key: peer.public_key, preshared_key: peer.preshared_key,
    allowed_ips: peer.allowed_ips,
    client_allowed_ips: peer.client_allowed_ips || '',
    endpoint: peer.endpoint,
    persistent_keepalive: peer.persistent_keepalive,
    description: peer.description, enabled: peer.enabled,
  } : {
    name: '', public_key: '', preshared_key: null,
    allowed_ips: suggestNextPeerIp(cfg, peers),
    client_allowed_ips: '0.0.0.0/0, ::/0',
    endpoint: null,
    persistent_keepalive: 0, description: null, enabled: true,
  })
  const [generatedPriv, setGeneratedPriv] = useState<string | null>(null)
  const [showPsk, setShowPsk] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [autoGenAttempted, setAutoGenAttempted] = useState(false)

  // For a fresh peer, generate keys automatically on open so the admin
  // does not have to think about it. Editing an existing peer keeps the
  // stored public key.
  useEffect(() => {
    if (peer || autoGenAttempted) return
    setAutoGenAttempted(true)
    ;(async () => {
      try {
        const kp = await api.wireguard.generateKeypair()
        setGeneratedPriv(kp.private_key)
        setForm((f) => ({ ...f, public_key: kp.public_key }))
      } catch { /* user can still paste manually via Advanced */ }
    })()
  }, [peer, autoGenAttempted])

  const regenerateKeys = async () => {
    const kp = await api.wireguard.generateKeypair()
    setGeneratedPriv(kp.private_key)
    setForm((f) => ({ ...f, public_key: kp.public_key }))
  }

  const generatePsk = async () => {
    const r = await api.wireguard.generatePsk()
    setForm({ ...form, preshared_key: r.preshared_key })
  }

  return (
    <Modal open={true} size="lg" onClose={onClose} title={peer ? `Edit peer ${peer.name}` : 'New WireGuard peer'}>
      <div className="space-y-3">
        <Field label="Name" hint="A label for this peer, e.g. laptop-jerome or site-paris">
          <input className="input" value={form.name} autoFocus
            onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </Field>

        <Field label="Tunnel address" hint="The IP this peer gets on the WG tunnel. /32 for a road-warrior.">
          <input className="input font-mono text-sm" value={form.allowed_ips}
            placeholder="10.10.0.2/32"
            onChange={(e) => setForm({ ...form, allowed_ips: e.target.value })} />
        </Field>

        <Field
          label="Client routes (what the client can reach)"
          hint="Networks the client routes through the tunnel. Full tunnel: 0.0.0.0/0, ::/0. Split tunnel: list the LANs, e.g. 10.10.0.0/24, 192.168.1.0/24."
        >
          <input className="input font-mono text-sm"
            placeholder="0.0.0.0/0, ::/0"
            value={form.client_allowed_ips}
            onChange={(e) => setForm({ ...form, client_allowed_ips: e.target.value })} />
        </Field>

        <div className="flex items-center gap-2 text-sm">
          <Toggle checked={form.enabled}
            onChange={(v) => setForm({ ...form, enabled: v })} />
          <span>Peer enabled</span>
        </div>

        {!peer && (
          <div className="text-xs text-gray-600 bg-gray-50 border border-gray-200 rounded px-2 py-2">
            A new keypair will be generated for this peer. The private key is shown
            once after saving (with the ready-to-paste client config), then discarded.
          </div>
        )}

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
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <div className="text-sm font-medium">Peer public key</div>
                {!peer && (
                  <button className="btn-ghost py-1 text-xs" type="button" onClick={regenerateKeys}>
                    Regenerate
                  </button>
                )}
              </div>
              <input className="input font-mono text-xs" value={form.public_key}
                placeholder="Paste the public key if generated elsewhere"
                onChange={(e) => setForm({ ...form, public_key: e.target.value })} />
              {generatedPriv && (
                <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded p-2 mt-1">
                  <div className="font-medium mb-1">Peer private key (will not be stored, shown again after save):</div>
                  <div className="font-mono break-all">{generatedPriv}</div>
                </div>
              )}
            </div>

            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <div className="text-sm font-medium">Pre-shared key (optional)</div>
                <div className="flex items-center gap-2">
                  {form.preshared_key && (
                    <button
                      className="text-xs text-gray-700 hover:text-gray-900 underline"
                      onClick={() => setShowPsk((v) => !v)}
                      type="button"
                    >{showPsk ? 'Hide' : 'Show'}</button>
                  )}
                  <button className="btn-ghost py-1 text-xs" type="button" onClick={generatePsk}>
                    Generate
                  </button>
                </div>
              </div>
              <input
                className="input font-mono text-xs"
                type={showPsk ? 'text' : 'password'}
                autoComplete="off"
                value={form.preshared_key || ''}
                onChange={(e) => setForm({ ...form, preshared_key: e.target.value || null })}
              />
            </div>

            <Field label="Extra networks routed through this peer" hint="Site-to-site only. CIDRs separated by commas.">
              <input className="input font-mono text-sm"
                placeholder="192.168.42.0/24"
                value={(form.allowed_ips.split(',').slice(1).join(',')).trim()}
                onChange={(e) => {
                  const first = form.allowed_ips.split(',')[0] || ''
                  const rest = e.target.value.trim()
                  setForm({ ...form, allowed_ips: rest ? `${first}, ${rest}` : first })
                }} />
            </Field>

            <Field label="Remote endpoint" hint="host:port. Only for site-to-site.">
              <input className="input font-mono text-sm" value={form.endpoint || ''}
                onChange={(e) => setForm({ ...form, endpoint: e.target.value || null })} />
            </Field>

            <Field label="Keepalive (seconds)" hint="25 is typical behind NAT. 0 disables.">
              <input type="number" className="input" value={form.persistent_keepalive}
                onChange={(e) => setForm({ ...form, persistent_keepalive: parseInt(e.target.value) || 0 })} />
            </Field>

            <Field label="Description">
              <input className="input" value={form.description || ''}
                onChange={(e) => setForm({ ...form, description: e.target.value || null })} />
            </Field>
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={() => onSave(form, generatedPriv)}>Save</button>
        </div>
      </div>
    </Modal>
  )
}




