import { useEffect, useState } from 'react'
import {
  api,
  type IpsecStatus,
  type IpsecConnection,
  type IpsecConnectionInput,
  type IpsecCa,
  type IpsecCert,
} from '../lib/api'
import PageHeader from '../components/PageHeader'
import ApplyServiceButton from '../components/ApplyServiceButton'
import { FileBadge, KeyRound } from 'lucide-react'
import EmptyState from '../components/EmptyState'
import { fmt } from '../lib/format'
import Toggle from '../components/Toggle'
import Modal from '../components/Modal'
import { useConfirm } from '../components/ConfirmModal'
import { ErrorBlock, SuccessBlock } from '../components/Alerts'
import { ServiceStatusInline, type ServiceState } from '../components/ServiceStatusLine'
import CardHeader from '../components/CardHeader'
import { useNavigate, useParams } from 'react-router-dom'

// Sub-routes : /vpn/ipsec/<tab>. The page is split in three tabs so the
// status + connections list, the PKI panel and the (future) per-user
// auth view do not crowd the same screen. Bookmarks survive across
// reloads and the browser back-button works inside the page.
type IpsecTab = 'connections' | 'certificates' | 'users'

const IPSEC_TABS: { key: IpsecTab; label: string }[] = [
  { key: 'connections', label: 'Connections' },
  { key: 'certificates', label: 'Certificates' },
  { key: 'users', label: 'Users' },
]

function IpsecTabs({ tab, onChange }: { tab: IpsecTab; onChange: (t: IpsecTab) => void }) {
  return (
    <div className="flex border-b border-gray-200">
      {IPSEC_TABS.map((t) => {
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

export default function IPsec() {
  const params = useParams<{ tab?: string }>()
  const nav = useNavigate()
  const validTabs: IpsecTab[] = ['connections', 'certificates', 'users']
  const tab: IpsecTab = (params.tab && (validTabs as string[]).includes(params.tab))
    ? (params.tab as IpsecTab)
    : 'connections'
  // Canonicalize a bare /vpn/ipsec to /vpn/ipsec/connections so bookmarks
  // stay stable and tab navigation reflects the URL.
  useEffect(() => {
    if (!params.tab || !(validTabs as string[]).includes(params.tab)) {
      nav('/vpn/ipsec/connections', { replace: true })
    }
  }, [params.tab, nav])
  const setTab = (k: IpsecTab) => nav(`/vpn/ipsec/${k}`)

  const [status, setStatus] = useState<IpsecStatus | null>(null)
  const [connections, setConnections] = useState<IpsecConnection[]>([])
  const [ca, setCa] = useState<IpsecCa | null>(null)
  const [certs, setCerts] = useState<IpsecCert[]>([])
  const [busy, setBusy] = useState(false)
  // Distinct from `busy`: only flips while the operator clicks the
  // service on/off toggle, so the small spinner next to the toggle in
  // the PageHeader does not also fire during a regular Apply or
  // cert/connection edit.
  const [toggleBusy, setToggleBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [editing, setEditing] = useState<IpsecConnection | null>(null)
  const [creating, setCreating] = useState(false)
  const [showCaModal, setShowCaModal] = useState(false)
  const [creatingCert, setCreatingCert] = useState(false)
  const [importingCert, setImportingCert] = useState(false)
  const { confirm: confirmFn, ConfirmHost } = useConfirm()

  const reload = async () => {
    try {
      const [s, c, theCa, theCerts] = await Promise.all([
        api.ipsec.status(),
        api.ipsec.listConnections(),
        api.ipsec.getCa(),
        api.ipsec.listCerts(),
      ])
      setStatus(s); setConnections(c); setCa(theCa); setCerts(theCerts)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  useEffect(() => { reload() }, [])
  useEffect(() => {
    const id = setInterval(() => {
      api.ipsec.status().then(setStatus).catch(() => {})
    }, 5000)
    return () => clearInterval(id)
  }, [])

  const installPkgs = async () => {
    setBusy(true); setError(null); setMessage(null)
    try {
      const r = await api.ipsec.install()
      setMessage(r.newly_installed.length > 0
        ? `Installed packages : ${r.newly_installed.join(', ')}.`
        : r.installed ? 'Packages already installed.' : (r.output_tail || 'Dry-run.'))
      await reload()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const serviceInstalled = !!(status?.installed)
  const globallyEnabled = status?.globally_enabled ?? true

  const toggleGlobal = async () => {
    const next = !globallyEnabled
    const ok = await confirmFn(next ? {
      title: 'Enable IPsec server ?',
      message: 'strongSwan will be started now and re-applied at every boot. Saved enabled connections will be brought up.',
      confirmLabel: 'Enable',
    } : {
      title: 'Disable IPsec server ?',
      message: 'strongSwan will be stopped immediately. All active SAs will be torn down and the daemon will not start at boot until you re-enable it. The configuration is preserved.',
      confirmLabel: 'Disable',
      destructive: true,
    })
    if (!ok) return
    setToggleBusy(true); setError(null); setMessage(null)
    try {
      await api.ipsec.setConfig({ enabled: next })
      setMessage(next
        ? 'IPsec server enabled. Saved connections re-applied.'
        : 'IPsec server disabled. strongSwan stopped and will not start at boot.')
      await reload()
    } catch (e) { setError((e as Error).message) } finally { setToggleBusy(false) }
  }

  return (
    <div>
      <PageHeader
        icon={<KeyRound size={16} />}
        title="IPsec (strongSwan)"
        description="IPsec/IKEv2 site-to-site and roadwarrior tunnels."
        status={status && (
          <ServiceStatusInline
            state={(status.service_state ?? (status.service_active ? 'active' : 'inactive')) as ServiceState}
            version={status.version}
          />
        )}
        serviceEnabled={globallyEnabled}
        serviceToggleBusy={toggleBusy || !serviceInstalled}
        serviceToggleTitle={globallyEnabled
          ? 'IPsec server enabled. Click to stop strongSwan and disable it at boot.'
          : 'IPsec server disabled. Click to start strongSwan and re-apply saved connections.'}
        onServiceEnabledChange={toggleGlobal}
        actions={
          <div className="flex items-center gap-3">
            {/*
              The transient Start/Stop buttons used to live here, but they
              duplicated the service-enabled toggle in the header (which
              also start/stops the daemon) and confused the admin. The
              header toggle covers enable+start / disable+stop and Apply
              covers the reload-after-config-change path, which is
              everything we need.
            */}
            <ApplyServiceButton
              service="ipsec"
              pendingTooltip="Reload swanctl to apply the saved configuration."
              onApplied={() => { void reload(); setMessage('strongSwan reloaded.') }}
              onError={setError}
              disabled={!serviceInstalled}
            />
          </div>
        }
      />

      <div className="px-6 py-4 space-y-6">
        <IpsecTabs tab={tab} onChange={setTab} />

        {error && <ErrorBlock message={error} />}
        {message && <SuccessBlock message={message} onDismiss={() => setMessage(null)} />}

        {tab === 'connections' && status && !status.installed && (
          <div className="bg-amber-50 border border-amber-200 text-amber-900 px-3 py-3 rounded text-sm">
            <div className="flex items-start justify-between gap-3 flex-wrap">
              <div>
                <div className="font-medium">Missing packages on this node</div>
                <div className="mt-1">To install: <code>strongswan strongswan-swanctl</code>.</div>
              </div>
              <button className="btn-primary whitespace-nowrap" onClick={installPkgs} disabled={busy}>
                {busy ? 'Installing...' : 'Install now'}
              </button>
            </div>
          </div>
        )}

        {tab === 'connections' && status && status.active_sas.length > 0 && (
          <div className="card">
            <CardHeader title="Active Security Associations" />
            <table className="w-full text-sm">
              <thead className="text-left text-gray-600 border-b">
                <tr><th className="py-2">Login</th><th>State</th><th>Details</th></tr>
              </thead>
              <tbody>
                {status.active_sas.map((sa, i) => (
                  <tr key={`${sa.name}-${i}`} className="border-b last:border-0">
                    <td className="py-2 font-mono">{sa.name}</td>
                    <td>
                      <span className={`text-xs px-2 py-1 rounded font-mono border ${
                        sa.state === 'ESTABLISHED' ? 'bg-emerald-50 border-emerald-300 text-emerald-800' :
                        'bg-amber-50 border-amber-300 text-amber-800'
                      }`}>{sa.state}</span>
                    </td>
                    <td className="text-xs font-mono text-gray-600">{sa.details}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {tab === 'certificates' && <PkiPanel
          ca={ca}
          certs={certs}
          onGenerateCa={() => setShowCaModal(true)}
          onNewCert={() => setCreatingCert(true)}
          onImportCert={() => setImportingCert(true)}
          onRevoke={async (id) => {
            const cert = certs.find((c) => c.id === id)
            const ok = await confirmFn({
              title: 'Revoke certificate',
              message: cert ? <p>Certificate <span className="font-mono">{cert.name}</span> will be added to the CRL. Existing tunnels using it stop authenticating.</p> : 'This certificate will be revoked.',
              destructive: true,
              confirmLabel: 'Revoke',
              requireText: 'revoke',
            })
            if (!ok) return
            try { await api.ipsec.revokeCert(id); await reload() } catch (e) { setError((e as Error).message) }
          }}
          onDelete={async (id) => {
            const cert = certs.find((c) => c.id === id)
            const ok = await confirmFn({
              title: 'Delete certificate',
              message: cert ? <p>Certificate <span className="font-mono">{cert.name}</span> will be permanently deleted.</p> : 'This certificate will be deleted.',
              destructive: true,
              requireText: 'delete',
            })
            if (!ok) return
            try { await api.ipsec.deleteCert(id); await reload() } catch (e) { setError((e as Error).message) }
          }}
        />}

        {tab === 'users' && (
          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-lg font-semibold">Per-user authentication</h2>
            </div>
            <div className="text-sm text-gray-700">
              <p className="mb-2">
                Roadwarrior IPsec users (EAP-MSCHAPv2 or PSK by login) will be managed here.
              </p>
              <p className="text-xs text-gray-600">
                Planned for v1.1, alongside the LDAP / AD integration. Today, define static
                <code className="font-mono mx-1">connections</code> with embedded credentials
                from the Connections tab.
              </p>
            </div>
          </div>
        )}

        {tab === 'connections' && (
        <div className="card">
          <div className="flex items-center justify-between mb-3 gap-2">
            <h2 className="text-lg font-semibold">IPsec connections</h2>
            <button className="btn-primary" onClick={() => setCreating(true)}>
              New connection
            </button>
          </div>
          {connections.length === 0 ? (
            <EmptyState
              icon={<KeyRound size={20} />}
              text="No IPsec connection"
              hint="Declare a site-to-site tunnel or roadwarrior access."
              action={<button className="btn-primary" onClick={() => setCreating(true)}>New connection</button>}
            />
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-gray-600 border-b">
                <tr>
                  <th className="py-2">Name</th>
                  <th>Remote</th>
                  <th>Traffic selectors</th>
                  <th>State</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {connections.map((c) => (
                  <tr key={c.id} className="border-b last:border-0">
                    <td className="py-2">
                      <div className="font-medium">{c.name}</div>
                      {c.description && <div className="text-xs text-gray-600">{c.description}</div>}
                    </td>
                    <td className="font-mono text-xs">{c.remote_addrs}</td>
                    <td className="font-mono text-xs">
                      {c.local_ts}<br /><span className="text-gray-500">{c.remote_ts}</span>
                    </td>
                    <td>
                      <span className={`text-xs px-2 py-1 rounded border ${
                        c.enabled ? 'bg-emerald-50 border-emerald-300 text-emerald-800' :
                                    'bg-slate-50 border-slate-300 text-slate-600'
                      }`}>{c.enabled ? 'enabled' : 'disabled'}</span>
                    </td>
                    <td className="text-right space-x-2">
                      <button className="btn-ghost py-1" onClick={() => setEditing(c)}>Edit</button>
                      <button className="btn-ghost py-1 text-red-700 hover:text-red-800" onClick={async () => {
                        const ok = await confirmFn({
                          title: `Delete connection "${c.name}"`,
                          message: <p>The IPsec connection <span className="font-mono">{c.name}</span> will be removed from the strongSwan config and the DB.</p>,
                          destructive: true,
                          requireText: c.name,
                        })
                        if (!ok) return
                        try { await api.ipsec.deleteConnection(c.id); await reload() }
                        catch (e) { setError((e as Error).message) }
                      }}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        )}
      </div>

      {(creating || editing) && (
        <ConnectionModal
          conn={editing}
          certs={certs}
          connections={connections}
          hasCa={!!ca}
          onClose={() => { setCreating(false); setEditing(null) }}
          onSave={async (data) => {
            try {
              if (editing) await api.ipsec.updateConnection(editing.id, data)
              else await api.ipsec.createConnection(data)
              setCreating(false); setEditing(null)
              // Apply automatique pour pousser dans swanctl
              const r = await api.ipsec.apply()
              setMessage(r.message)
              await reload()
            } catch (e) { setError((e as Error).message) }
          }}
        />
      )}

      {showCaModal && (
        <CaModal
          ca={ca}
          onClose={() => setShowCaModal(false)}
          onGenerate={async (data) => {
            try {
              await api.ipsec.generateCa(data)
              setShowCaModal(false)
              await reload()
            } catch (e) { setError((e as Error).message) }
          }}
        />
      )}

      {creatingCert && (
        <NewCertModal
          onClose={() => setCreatingCert(false)}
          onCreate={async (data) => {
            try {
              await api.ipsec.createCert(data)
              setCreatingCert(false)
              await reload()
            } catch (e) { setError((e as Error).message) }
          }}
        />
      )}

      {importingCert && (
        <ImportCertModal
          onClose={() => setImportingCert(false)}
          onImport={async (data) => {
            try {
              await api.ipsec.importCert(data)
              setImportingCert(false)
              await reload()
            } catch (e) { setError((e as Error).message) }
          }}
        />
      )}
      <ConfirmHost />
    </div>
  )
}

function PkiPanel({ ca, certs, onGenerateCa, onNewCert, onImportCert, onRevoke, onDelete }: {
  ca: IpsecCa | null
  certs: IpsecCert[]
  onGenerateCa: () => void
  onNewCert: () => void
  onImportCert: () => void
  onRevoke: (id: number) => Promise<void>
  onDelete: (id: number) => Promise<void>
}) {
  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">Built-in PKI (certificate mode)</h2>
        <div className="flex gap-2">
          {!ca && (
            <button className="btn-primary" onClick={onGenerateCa}>Generate root CA</button>
          )}
          {ca && (
            <>
              <button className="btn-secondary" onClick={onGenerateCa}>Regenerate the CA</button>
              <button className="btn-secondary" onClick={onImportCert}>Import a remote cert</button>
              <button className="btn-primary" onClick={onNewCert}>New certificate</button>
            </>
          )}
        </div>
      </div>

      {!ca ? (
        <div className="text-sm text-gray-600">
          To use certificate mode in an IPsec connection, generate
          the root CA first. Peer certs will then be signed by
          this CA.
        </div>
      ) : (
        <>
          <div className="bg-slate-50 border border-slate-200 rounded p-3 text-sm mb-3">
            <div className="font-medium">{ca.subject_cn}</div>
            <div className="text-xs text-gray-600 mt-1">
              Organization: {ca.subject_o} | Valid until:{' '}
              {fmt.date(ca.expires_at)}
            </div>
            <details className="mt-2">
              <summary className="text-xs cursor-pointer text-blue-700">Show PEM certificate</summary>
              <textarea readOnly className="w-full font-mono text-xs mt-2 h-32" value={ca.cert_pem} />
              <a className="btn-secondary text-xs mt-2 inline-block"
                href={`data:text/plain;charset=utf-8,${encodeURIComponent(ca.cert_pem)}`}
                download="muros-ca.pem">Download muros-ca.pem</a>
            </details>
          </div>

          {certs.length === 0 ? (
            <EmptyState
              icon={<FileBadge size={20} />}
              text="No certificate"
              hint="Generate a certificate for this IPsec gateway, or import an existing chain."
              action={<button className="btn-primary" onClick={onNewCert}>New certificate</button>}
            />
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-gray-600 border-b">
                <tr>
                  <th className="py-2">Name</th>
                  <th>Type</th>
                  <th>Expire</th>
                  <th>State</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {certs.map((c) => (
                  <tr key={c.id} className="border-b last:border-0">
                    <td className="py-2">
                      <div className="font-medium">{c.name}</div>
                      <div className="text-xs text-gray-600">{c.subject_cn}{c.san ? ` (${c.san})` : ''}</div>
                    </td>
                    <td>
                      <span className="text-xs font-mono">
                        {c.is_local ? 'local + key' : 'remote (cert only)'}
                      </span>
                    </td>
                    <td className="text-xs font-mono">
                      {fmt.date(c.expires_at)}
                    </td>
                    <td>
                      <span className={`text-xs px-2 py-1 rounded border ${
                        c.revoked
                          ? 'bg-red-50 border-red-300 text-red-800'
                          : 'bg-emerald-50 border-emerald-300 text-emerald-800'
                      }`}>
                        {c.revoked ? 'revoked' : 'active'}
                      </span>
                    </td>
                    <td className="text-right space-x-2">
                      <a className="btn-secondary text-xs"
                        href={`data:text/plain;charset=utf-8,${encodeURIComponent(c.cert_pem)}`}
                        download={`muros-${c.name}.pem`}>Cert</a>
                      {!c.revoked && (
                        <button className="btn-ghost py-1" onClick={() => onRevoke(c.id)}>Revoquer</button>
                      )}
                      <button className="btn-ghost py-1 text-red-700 hover:text-red-800" onClick={() => onDelete(c.id)}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  )
}

function CaModal({ ca, onClose, onGenerate }: {
  ca: IpsecCa | null
  onClose: () => void
  onGenerate: (data: { subject_cn: string; subject_o: string; validity_days: number }) => Promise<void>
}) {
  const [form, setForm] = useState({
    subject_cn: ca?.subject_cn || 'MurOS Root CA',
    subject_o: ca?.subject_o || 'MurOS',
    validity_days: ca?.validity_days || 3650,
  })
  return (
    <Modal open={true} size="md" onClose={onClose} title={ca ? 'Regenerate the CA' : 'Generate root CA'}>
      <div className="space-y-3">
        {ca && (
          <div className="bg-amber-50 border border-amber-200 text-amber-900 px-3 py-2 rounded text-sm">
            Regenerating the CA invalidates ALL previously signed certificates. They
            will need to be regenerated and redeployed to the peers.
          </div>
        )}
        <Field label="Common Name (CN)">
          <input className="input" value={form.subject_cn}
            onChange={(e) => setForm({ ...form, subject_cn: e.target.value })} />
        </Field>
        <Field label="Organisation (O)">
          <input className="input" value={form.subject_o}
            onChange={(e) => setForm({ ...form, subject_o: e.target.value })} />
        </Field>
        <Field label="Validity (days)" hint="3650 = 10 years">
          <input type="number" className="input" value={form.validity_days}
            onChange={(e) => setForm({ ...form, validity_days: parseInt(e.target.value) || 3650 })} />
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={() => onGenerate(form)}>
            {ca ? 'Regenerate' : 'Generate'}
          </button>
        </div>
      </div>
    </Modal>
  )
}

function NewCertModal({ onClose, onCreate }: {
  onClose: () => void
  onCreate: (data: { name: string; subject_cn: string; san: string | null; validity_days: number; is_local: boolean }) => Promise<void>
}) {
  const [form, setForm] = useState({
    name: '', subject_cn: '', san: '' as string,
    validity_days: 825,
  })
  const [showAdvanced, setShowAdvanced] = useState(false)
  return (
    <Modal open={true} size="md" onClose={onClose} title="New certificate">
      <div className="space-y-3">
        <Field label="Name" hint="Short identifier used in tunnels (e.g. fw-paris, site-lyon). Also used as the Common Name unless overridden.">
          <input className="input" value={form.name} autoFocus
            placeholder="fw-paris"
            onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </Field>

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
            <Field label="Common Name (CN)" hint="Defaults to the name above. Override to use an FQDN.">
              <input className="input" value={form.subject_cn}
                placeholder={form.name || '(same as name)'}
                onChange={(e) => setForm({ ...form, subject_cn: e.target.value })} />
            </Field>
            <Field label="Subject Alternative Names" hint="DNS:fw.example.com,IP:203.0.113.5 (comma-separated)">
              <input className="input font-mono text-sm" value={form.san}
                onChange={(e) => setForm({ ...form, san: e.target.value })} />
            </Field>
            <Field label="Validity (days)" hint="825 = ~27 months">
              <input type="number" className="input" value={form.validity_days}
                onChange={(e) => setForm({ ...form, validity_days: parseInt(e.target.value) || 825 })} />
            </Field>
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary"
            disabled={!form.name.trim()}
            onClick={() =>
              onCreate({
                name: form.name.trim(),
                subject_cn: (form.subject_cn || form.name).trim(),
                san: form.san || null,
                validity_days: form.validity_days,
                is_local: true,
              })
            }>Generate</button>
        </div>
      </div>
    </Modal>
  )
}

function ImportCertModal({ onClose, onImport }: {
  onClose: () => void
  onImport: (data: { name: string; cert_pem: string }) => Promise<void>
}) {
  const [form, setForm] = useState({ name: '', cert_pem: '' })
  return (
    <Modal open={true} size="lg" onClose={onClose} title="Import a remote certificate">
      <div className="space-y-3">
        <div className="text-sm text-gray-600">
          Import a remote peer's PEM certificate. Will be used to
          validate its identity (in addition to CA validation).
        </div>
        <Field label="Internal name">
          <input className="input" value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </Field>
        <Field label="PEM certificate" hint="Paste the .pem file content (BEGIN CERTIFICATE / END CERTIFICATE)">
          <textarea className="input font-mono text-xs h-40" value={form.cert_pem}
            onChange={(e) => setForm({ ...form, cert_pem: e.target.value })} />
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={() => onImport(form)}>Import</button>
        </div>
      </div>
    </Modal>
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

function suggestNextConnectionName(existing: IpsecConnection[]): string {
  const taken = new Set(existing.map((c) => c.name))
  for (let i = 1; i < 1000; i++) {
    const candidate = `tunnel${i}`
    if (!taken.has(candidate)) return candidate
  }
  return 'tunnel'
}

function ConnectionModal({ conn, certs, hasCa, connections, onClose, onSave }: {
  conn: IpsecConnection | null
  certs: IpsecCert[]
  hasCa: boolean
  connections: IpsecConnection[]
  onClose: () => void
  onSave: (data: IpsecConnectionInput) => Promise<void>
}) {
  const [form, setForm] = useState<IpsecConnectionInput>(conn ? {
    name: conn.name, auth_mode: conn.auth_mode || 'psk',
    local_addrs: conn.local_addrs, remote_addrs: conn.remote_addrs,
    local_id: conn.local_id, remote_id: conn.remote_id, psk: conn.psk,
    local_cert_id: conn.local_cert_id, remote_cert_id: conn.remote_cert_id,
    local_ts: conn.local_ts, remote_ts: conn.remote_ts,
    ike_proposals: conn.ike_proposals, esp_proposals: conn.esp_proposals,
    start_action: conn.start_action, description: conn.description, enabled: conn.enabled,
  } : {
    name: suggestNextConnectionName(connections), auth_mode: 'psk',
    local_addrs: '%any', remote_addrs: '',
    local_id: null, remote_id: null, psk: '',
    local_cert_id: null, remote_cert_id: null,
    local_ts: '0.0.0.0/0', remote_ts: '0.0.0.0/0',
    ike_proposals: 'aes256-sha256-modp2048', esp_proposals: 'aes256-sha256',
    start_action: 'start', description: null, enabled: true,
  })

  const localCerts = certs.filter((c) => c.is_local && !c.revoked)
  const remoteCerts = certs.filter((c) => !c.is_local && !c.revoked)

  const generatePsk = () => {
    const arr = new Uint8Array(32)
    crypto.getRandomValues(arr)
    const b64 = btoa(String.fromCharCode(...arr))
    setForm({ ...form, psk: b64 })
  }

  const [showAdvanced, setShowAdvanced] = useState(false)

  // Basic vs advanced split: the basic form (name + remote address +
  // auth + networks) is enough for a typical site-to-site tunnel
  // between two standards-compliant IPsec endpoints. IKE/ESP proposals,
  // local IDs, startup action and listen address only show up when
  // the operator explicitly opens Advanced. Defaults are
  // strongSwan-recommended: AES-256 + SHA-256 + DH-2048 / IKEv2 with
  // a strong PSK, started automatically.

  return (
    <Modal open={true} size="lg" onClose={onClose} title={conn ? `Edit ${conn.name}` : 'New IPsec connection'}>
      <div className="space-y-3">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Field label="Name">
            <input className="input" value={form.name} disabled={!!conn}
              placeholder="site-paris, branch-lyon..."
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label="Remote address" hint="IP or FQDN of the peer. Use %any for roadwarriors.">
            <input className="input font-mono text-sm" value={form.remote_addrs}
              placeholder="203.0.113.10"
              onChange={(e) => setForm({ ...form, remote_addrs: e.target.value })} />
          </Field>

          <Field label="Local networks" hint="Behind this firewall. 0.0.0.0/0 routes everything.">
            <input className="input font-mono text-sm" value={form.local_ts}
              onChange={(e) => setForm({ ...form, local_ts: e.target.value })} />
          </Field>
          <Field label="Remote networks" hint="Behind the peer. Must mirror local on its side.">
            <input className="input font-mono text-sm" value={form.remote_ts}
              onChange={(e) => setForm({ ...form, remote_ts: e.target.value })} />
          </Field>
        </div>

        <div className="border border-slate-200 rounded p-3 bg-slate-50">
          <div className="flex items-center gap-3 mb-3">
            <span className="text-sm font-medium">Authentication</span>
            <label className="flex items-center gap-1.5 text-sm">
              <input type="radio" name="auth_mode" value="psk"
                checked={form.auth_mode === 'psk'}
                onChange={() => setForm({ ...form, auth_mode: 'psk' })} />
              Pre-shared key
            </label>
            <label className={`flex items-center gap-1.5 text-sm ${!hasCa ? 'opacity-50' : ''}`}>
              <input type="radio" name="auth_mode" value="cert"
                checked={form.auth_mode === 'cert'}
                disabled={!hasCa}
                onChange={() => setForm({ ...form, auth_mode: 'cert' })} />
              X.509 certificate
            </label>
            {!hasCa && (
              <span className="text-xs text-amber-700">
                (generate the CA first to enable)
              </span>
            )}
          </div>

          {form.auth_mode === 'psk' ? (
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <div className="text-sm font-medium">Pre-shared key</div>
                <button className="btn-ghost py-1 text-xs" type="button" onClick={generatePsk}>
                  Generate
                </button>
              </div>
              <input className="input font-mono text-xs" value={form.psk}
                onChange={(e) => setForm({ ...form, psk: e.target.value })}
                placeholder="Shared secret with the peer" />
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <Field label="Local certificate">
                <select className="input" value={form.local_cert_id ?? ''}
                  onChange={(e) => setForm({ ...form, local_cert_id: e.target.value ? parseInt(e.target.value) : null })}>
                  <option value="">-- choose a local cert --</option>
                  {localCerts.map((c) => (
                    <option key={c.id} value={c.id}>{c.name} ({c.subject_cn})</option>
                  ))}
                </select>
              </Field>
              <Field label="Expected remote cert (optional)" hint="Pin peer identity to this exact cert">
                <select className="input" value={form.remote_cert_id ?? ''}
                  onChange={(e) => setForm({ ...form, remote_cert_id: e.target.value ? parseInt(e.target.value) : null })}>
                  <option value="">-- validate via CA only --</option>
                  {remoteCerts.map((c) => (
                    <option key={c.id} value={c.id}>{c.name} ({c.subject_cn})</option>
                  ))}
                </select>
              </Field>
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 text-sm">
          <Toggle checked={form.enabled}
            onChange={(v) => setForm({ ...form, enabled: v })} />
          <span>Tunnel enabled</span>
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
            <Field label="Description">
              <input className="input" value={form.description || ''}
                onChange={(e) => setForm({ ...form, description: e.target.value || null })} />
            </Field>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <Field label="Listen address" hint="Local IP/host or %any to listen on all IPs">
                <input className="input font-mono text-sm" value={form.local_addrs}
                  onChange={(e) => setForm({ ...form, local_addrs: e.target.value })} />
              </Field>
              <Field label="Startup action" hint="start = initiate, trap = on traffic, none = passive">
                <select className="input" value={form.start_action}
                  onChange={(e) => setForm({ ...form, start_action: e.target.value })}>
                  <option value="start">start</option>
                  <option value="trap">trap</option>
                  <option value="none">none (passive)</option>
                </select>
              </Field>

              <Field label="Local ID (optional)" hint="Defaults to the local address">
                <input className="input font-mono text-sm" value={form.local_id || ''}
                  onChange={(e) => setForm({ ...form, local_id: e.target.value || null })} />
              </Field>
              <Field label="Remote ID (optional)" hint="Defaults to the remote address">
                <input className="input font-mono text-sm" value={form.remote_id || ''}
                  onChange={(e) => setForm({ ...form, remote_id: e.target.value || null })} />
              </Field>

              <Field label="IKE proposals" hint="Phase 1 algorithms. The default aes256-sha256-modp2048 interops with most vendors.">
                <input className="input font-mono text-xs" value={form.ike_proposals}
                  onChange={(e) => setForm({ ...form, ike_proposals: e.target.value })} />
              </Field>
              <Field label="ESP proposals" hint="Phase 2 algorithms. Use aes256gcm for higher throughput when both sides support it.">
                <input className="input font-mono text-xs" value={form.esp_proposals}
                  onChange={(e) => setForm({ ...form, esp_proposals: e.target.value })} />
              </Field>
            </div>
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={() => onSave(form)}>Save</button>
        </div>
      </div>
    </Modal>
  )
}
