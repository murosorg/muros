import { useEffect, useRef, useState } from 'react'
import {
  api, type HttpConfig, type HttpConfigInput, type TlsStatus,
} from '../lib/api'
import PageHeader from '../components/PageHeader'
import { ServiceStatusInline, type ServiceState } from '../components/ServiceStatusLine'
import Toggle from '../components/Toggle'
import FormActions from '../components/FormActions'
import { isDirty } from '../lib/dirty'
import CardHeader from '../components/CardHeader'
import { ErrorBlock, SuccessBlock } from '../components/Alerts'
import { useConfirm } from '../components/ConfirmModal'
import { fmt } from '../lib/format'
import { Globe } from 'lucide-react'

export default function HttpAccess() {
  // The page-level Apply button at the top-right reflects whatever the
  // web-listener section (the only configure+apply section on this page)
  // exposes via a ref. Account and TLS sections have their own discrete
  // actions and are not driven by Apply.
  const listenApplyRef = useRef<{ apply: () => void; busy: boolean; dirty: boolean } | null>(null)
  const [, forceRerender] = useState(0)
  // nginx live status (polled every 3s) so the page header carries
  // the same "state pill + package version" widget the other service
  // pages (DHCP, DNS, SNMP, SSH, WG, IPsec, HA, Notifications) expose.
  const [nginxStatus, setNginxStatus] = useState<{ service_state: string; version: string | null } | null>(null)
  useEffect(() => {
    let mounted = true
    const reload = () => {
      api.http.status()
        .then((s) => { if (mounted) setNginxStatus({ service_state: s.service_state || 'unknown', version: s.version }) })
        .catch(() => { /* silent: keep last known state */ })
    }
    reload()
    const id = setInterval(reload, 3000)
    return () => { mounted = false; clearInterval(id) }
  }, [])
  return (
    <div>
      <PageHeader
        icon={<Globe size={16} />}
        title="HTTP Access"
        description="Web listener and TLS. Account credentials live in System > Accounts."
        status={nginxStatus && (
          <ServiceStatusInline
            state={(nginxStatus.service_state || 'inactive') as ServiceState}
            version={nginxStatus.version}
          />
        )}
        actions={
          <FormActions
            onApply={() => listenApplyRef.current?.apply()}
            busy={listenApplyRef.current?.busy ?? false}
            dirty={listenApplyRef.current?.dirty ?? false}
          />
        }
      />
      <div className="px-6 py-4 space-y-6">
        <ListenSection
          register={(api) => { listenApplyRef.current = api; forceRerender((x) => x + 1) }}
        />
        <TlsSection />
      </div>
    </div>
  )
}

// --- Web listener (HTTP / HTTPS) ---

type ListenApplyApi = { apply: () => void; busy: boolean; dirty: boolean }

function ListenSection({ register }: { register: (api: ListenApplyApi) => void }) {
  const [cfg, setCfg] = useState<HttpConfig | null>(null)
  const [form, setForm] = useState<HttpConfigInput | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  const reload = async () => {
    try {
      const c = await api.http.getConfig()
      setCfg(c)
      if (!form) {
        setForm({
          // MurOS always binds the UI on every interface (0.0.0.0).
          // Restricting who can reach it is done with firewall rules,
          // the OPNsense way, so there is no per-interface selector here.
          listen_address: '0.0.0.0',
          port_https: c.port_https,
          port_http: c.port_http,
          redirect_http_to_https: c.redirect_http_to_https,
        })
      }
    } catch (e) { setErr((e as Error).message) }
  }
  useEffect(() => { reload() }, [])

  const apply = async () => {
    if (!form) return
    setBusy(true); setErr(null); setMsg(null)
    try {
      await api.http.updateConfig(form)
      const r = await api.http.apply({ skip_rollback: !!form.skip_rollback })
      setMsg(r.message)
      // La modale globale RollbackModal poll /api/pending-apply et prendra
      // automatiquement le relais pour le countdown + confirm/rollback.
      await reload()
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  // Orange dot on the page-level Apply while form diverges from server.
  const dirty = isDirty(form, cfg && {
    // Always 0.0.0.0: the listen address is no longer user-selectable.
    listen_address: '0.0.0.0',
    port_https: cfg.port_https,
    port_http: cfg.port_http,
    redirect_http_to_https: cfg.redirect_http_to_https,
  })

  // Re-register every time apply/busy/dirty changes so the page header
  // reads up-to-date values.
  useEffect(() => {
    register({ apply, busy, dirty })
  }, [busy, dirty, form])

  if (!form) return null

  return (
    <div className="card">
      <CardHeader title="Web access" />

      {err && <div className="mb-3"><ErrorBlock message={err} /></div>}
      {msg && <SuccessBlock message={msg} onDismiss={() => setMsg(null)} />}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="md:col-span-3 text-xs text-gray-600">
          The web UI listens on every interface. Restrict who can reach it
          with firewall rules (input chain), the same way you control any
          other service.
        </div>
        <div>
          <div className="text-sm font-medium mb-1">Port HTTPS</div>
          <input type="number" className="input" value={form.port_https}
            onChange={(e) => setForm({ ...form, port_https: parseInt(e.target.value) || 443 })} />
        </div>
        <div>
          <div className="text-sm font-medium mb-1">Port HTTP</div>
          <input type="number" className="input" value={form.port_http}
            onChange={(e) => setForm({ ...form, port_http: parseInt(e.target.value) || 80 })} />
        </div>
        <div className="flex items-end gap-2 text-sm">
          <Toggle checked={form.redirect_http_to_https}
            onChange={(v) => setForm({ ...form, redirect_http_to_https: v })} />
          <span>Redirect HTTP to HTTPS</span>
        </div>
        {/* Option dangereuse : si l'utilisateur active le toggle, on bascule
            fond + bordure en amber pour materialiser visuellement le risque.
            Un texte fait office de label de risque sous le titre. */}
        {/* Positive-phrasing toggle: ON = MurOS reverts in 10 s if not
            confirmed (safe default). OFF = the change sticks even if the
            session is lost. We keep the underlying field `skip_rollback`
            unchanged in the API; this is just a UI inversion. */}
        <div className={`md:col-span-3 rounded p-3 text-sm flex items-start gap-3 border ${
          form.skip_rollback
            ? 'border-amber-300 bg-amber-50'
            : 'border-slate-200'
        }`}>
          <Toggle checked={!form.skip_rollback}
            onChange={(v) => setForm({ ...form, skip_rollback: !v })} />
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-medium">Automatic rollback on apply</span>
              {form.skip_rollback && (
                <span className="text-[10px] uppercase tracking-wider font-semibold px-1.5 py-0.5 rounded bg-amber-200 text-amber-900">
                  Risk
                </span>
              )}
            </div>
            <div className={`text-xs mt-0.5 ${form.skip_rollback ? 'text-amber-900' : 'text-slate-600'}`}>
              Keep enabled to revert automatically in 10 seconds if you do
              not confirm. Disable only when you change address or
              interface knowing you will lose your current session.
            </div>
          </div>
        </div>
      </div>

    </div>
  )
}

// --- TLS certificate ---

function TlsSection() {
  const [status, setStatus] = useState<TlsStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [certPem, setCertPem] = useState('')
  const [keyPem, setKeyPem] = useState('')

  const [showUpload, setShowUpload] = useState(false)
  const uploadRef = useRef<HTMLDivElement | null>(null)
  // Quand le panneau d'import s'ouvre, on scroll dessus : sinon il apparait
  // sous le pli (apres la grille des metadata du cert courant) et le clic
  // sur "Import a cert" donne l'impression de ne rien faire.
  useEffect(() => {
    if (showUpload && uploadRef.current) {
      uploadRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [showUpload])
  const { confirm, ConfirmHost } = useConfirm()

  const reload = async () => {
    try { setStatus(await api.tls.status()) } catch (e) { setErr((e as Error).message) }
  }
  useEffect(() => { reload() }, [])

  const upload = async () => {
    setBusy(true); setErr(null); setMsg(null)
    try {
      const r = await api.tls.upload({ cert_pem: certPem, key_pem: keyPem })
      setMsg(r.message)
      setCertPem(''); setKeyPem(''); setShowUpload(false)
      // RollbackModal global gere le countdown.
      await reload()
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  const regen = async () => {
    const ok = await confirm({
      title: 'Regenerate snakeoil certificate',
      message: 'A new self-signed cert is generated with the current hostname as CN. The browser may ask for a new TLS confirmation.',
      destructive: true,
      confirmLabel: 'Regenerate',
      requireText: 'regenerate',
    })
    if (!ok) return
    setBusy(true); setErr(null); setMsg(null)
    try {
      const r = await api.tls.regenerate()
      setMsg(r.message)
      // RollbackModal global gere le countdown.
      await reload()
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">TLS certificate</h2>
        <div className="flex gap-2">
          <button className="btn-secondary text-xs" onClick={regen} disabled={busy}>
            Regenerate snakeoil
          </button>
          <button className="btn-secondary text-xs" onClick={() => setShowUpload(!showUpload)}>
            {showUpload ? 'Cancel import' : 'Import a cert'}
          </button>
        </div>
      </div>

      {err && <div className="mb-3"><ErrorBlock message={err} /></div>}
      {msg && <SuccessBlock message={msg} onDismiss={() => setMsg(null)} />}

      {status && status.present && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
          <KV k="Subject CN" v={status.subject_cn || 'n/a'} />
          <KV k="Issuer CN" v={status.issuer_cn || 'n/a'} />
          <KV k="SAN" v={status.san.length ? status.san.join(', ') : 'none'} />
          <KV k="Self-signed" v={status.is_self_signed ? 'yes' : 'no'} />
          <KV k="Valid from" v={fmt.datetime(status.not_before)} />
          <KV k="Expires on" v={fmt.datetime(status.not_after)} />
          <KV k="Days remaining" v={status.days_remaining !== null
            ? <span className={status.days_remaining < 30 ? 'text-amber-700 font-medium' : ''}>{status.days_remaining}</span>
            : 'n/a'} />
          <KV k="Private key" v={status.key_present ? 'present' : 'missing'} />
          <div className="md:col-span-2">
            <div className="text-xs text-gray-600">SHA-256 fingerprint</div>
            <div className="font-mono text-xs text-gray-700 break-all">{status.fingerprint_sha256 || 'n/a'}</div>
          </div>
        </div>
      )}
      {status && !status.present && (
        <div className="bg-amber-50 border border-amber-200 text-amber-900 px-3 py-2 rounded text-sm">
          No certificate in /etc/nginx/ssl/. Use 'Generate self-signed' to create a cert.
        </div>
      )}

      {showUpload && (
        <div ref={uploadRef} className="mt-4 border border-slate-200 rounded p-3 bg-slate-50">
          <div className="text-sm font-medium mb-2">Import an existing certificate (PEM)</div>
          <div className="space-y-3">
            <div>
              <div className="text-sm font-medium mb-1">Certificate (.pem or .crt)</div>
              <textarea className="input font-mono text-xs h-32" value={certPem}
                placeholder="-----BEGIN CERTIFICATE-----..."
                onChange={(e) => setCertPem(e.target.value)} />
            </div>
            <div>
              <div className="text-sm font-medium mb-1">Private key (.pem or .key)</div>
              <textarea className="input font-mono text-xs h-32" value={keyPem}
                placeholder="-----BEGIN PRIVATE KEY-----..."
                onChange={(e) => setKeyPem(e.target.value)} />
            </div>
            <button className="btn-primary" onClick={upload} disabled={busy || !certPem || !keyPem}>
              {busy ? 'Installing...' : 'Install the certificate'}
            </button>
          </div>
        </div>
      )}

      <ConfirmHost />
    </div>
  )
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs text-gray-600">{k}</div>
      <div className="font-medium">{v}</div>
    </div>
  )
}


