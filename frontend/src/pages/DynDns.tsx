// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 MurOS contributors.
import { useEffect, useState } from 'react'
import {
  api, type DynDnsEntry, type DynDnsEntryInput, type DynDnsProviderPreset,
} from '../lib/api'
import PageHeader from '../components/PageHeader'
import { useConfirm } from '../components/ConfirmModal'
import { ErrorBlock, SuccessBlock } from '../components/Alerts'
import { Globe, RefreshCw, Plus, Pencil, Trash2 } from 'lucide-react'

const EMPTY: DynDnsEntryInput = {
  enabled: true, provider: 'noip', server: '', hostname: '',
  username: '', password: '', custom_url: '',
}

function StatusBadge({ status }: { status: string | null }) {
  const map: Record<string, string> = {
    good: 'bg-green-100 text-green-800',
    nochg: 'bg-slate-100 text-slate-700',
    error: 'bg-red-100 text-red-800',
  }
  const cls = (status && map[status]) || 'bg-slate-100 text-slate-500'
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${cls}`}>{status || 'never'}</span>
}

export default function DynDns() {
  const [entries, setEntries] = useState<DynDnsEntry[]>([])
  const [presets, setPresets] = useState<Record<string, DynDnsProviderPreset>>({})
  const [publicIp, setPublicIp] = useState<string | null>(null)
  const [editing, setEditing] = useState<DynDnsEntry | null>(null)
  const [form, setForm] = useState<DynDnsEntryInput | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const { confirm, ConfirmHost } = useConfirm()

  const reload = async () => {
    try {
      const [list, pr] = await Promise.all([api.dyndns.list(), api.dyndns.providers()])
      setEntries(list); setPresets(pr)
    } catch (e) { setError((e as Error).message) }
  }
  useEffect(() => { void reload() }, [])
  useEffect(() => { api.dyndns.publicIp().then((r) => setPublicIp(r.ip)).catch(() => {}) }, [])

  const openNew = () => { setEditing(null); setForm({ ...EMPTY }) }
  const openEdit = (e: DynDnsEntry) => {
    setEditing(e)
    setForm({
      enabled: e.enabled, provider: e.provider, server: e.server, hostname: e.hostname,
      username: e.username ?? '', password: '', custom_url: e.custom_url ?? '',
    })
  }
  const closeForm = () => { setForm(null); setEditing(null) }

  const save = async () => {
    if (!form) return
    setBusy(true); setError(null); setMessage(null)
    try {
      if (editing) await api.dyndns.update(editing.id, form)
      else await api.dyndns.create(form)
      setMessage('Saved.')
      closeForm(); await reload()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const remove = async (e: DynDnsEntry) => {
    const ok = await confirm({
      title: 'Delete this hostname?', message: `${e.hostname} will no longer be kept up to date.`,
      confirmLabel: 'Delete', destructive: true,
    })
    if (!ok) return
    setBusy(true); setError(null)
    try { await api.dyndns.remove(e.id); await reload() }
    catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }

  const updateNow = async (e: DynDnsEntry) => {
    setBusy(true); setError(null); setMessage(null)
    try {
      const r = await api.dyndns.updateNow(e.id)
      const res = r.results[0]
      setMessage(res ? `${e.hostname}: ${res.status}${res.error ? ` (${res.error})` : ''}` : 'Done.')
      await reload()
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }

  const updateAll = async () => {
    setBusy(true); setError(null); setMessage(null)
    try {
      const r = await api.dyndns.updateNowAll()
      setMessage(r.reason ? `No update: ${r.reason}.` : `Updated ${r.results.length} hostname(s) (IP ${r.ip}).`)
      await reload()
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }

  const isCustom = form?.provider === 'custom'

  return (
    <div>
      <PageHeader
        icon={<Globe size={16} />}
        title="Dynamic DNS"
        description="Keep hostnames pointed at this firewall's public IP."
        actions={
          <div className="flex items-center gap-2">
            <button className="btn-secondary" onClick={updateAll} disabled={busy}>
              <RefreshCw size={14} className="mr-1" /> Update now
            </button>
            <button className="btn-primary" onClick={openNew} disabled={busy}>
              <Plus size={14} className="mr-1" /> Add hostname
            </button>
          </div>
        }
      />

      <div className="px-6 py-4 space-y-4">
        {error && <ErrorBlock message={error} />}
        {message && <SuccessBlock message={message} onDismiss={() => setMessage(null)} />}

        <div className="text-sm text-gray-600">
          Detected public IP: <span className="font-mono">{publicIp ?? 'unknown'}</span>
        </div>

        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b">
                <th className="py-2 pr-3">Hostname</th>
                <th className="py-2 pr-3">Provider</th>
                <th className="py-2 pr-3">Enabled</th>
                <th className="py-2 pr-3">Last IP</th>
                <th className="py-2 pr-3">Status</th>
                <th className="py-2 pr-3">Last update</th>
                <th className="py-2 pr-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {entries.length === 0 && (
                <tr><td colSpan={7} className="py-6 text-center text-gray-400">No hostname configured yet.</td></tr>
              )}
              {entries.map((e) => (
                <tr key={e.id} className="border-b last:border-0">
                  <td className="py-2 pr-3 font-medium">{e.hostname}</td>
                  <td className="py-2 pr-3">{presets[e.provider]?.label ?? e.provider}</td>
                  <td className="py-2 pr-3">{e.enabled ? 'yes' : 'no'}</td>
                  <td className="py-2 pr-3 font-mono text-xs">{e.last_ip ?? '-'}</td>
                  <td className="py-2 pr-3"><StatusBadge status={e.last_status} /></td>
                  <td className="py-2 pr-3 text-xs text-gray-500">
                    {e.last_update_at ? new Date(e.last_update_at).toLocaleString() : '-'}
                  </td>
                  <td className="py-2 pr-3">
                    <div className="flex items-center justify-end gap-1">
                      <button className="icon-btn" title="Update now" onClick={() => updateNow(e)} disabled={busy}>
                        <RefreshCw size={15} />
                      </button>
                      <button className="icon-btn" title="Edit" onClick={() => openEdit(e)} disabled={busy}>
                        <Pencil size={15} />
                      </button>
                      <button className="icon-btn text-red-600" title="Delete" onClick={() => remove(e)} disabled={busy}>
                        <Trash2 size={15} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="text-xs text-gray-500">
          Hostnames refresh automatically every few minutes and whenever the public IP changes.
          Use "Update now" to push immediately.
        </div>
      </div>

      {form && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-40 p-4" onClick={closeForm}>
          <div className="bg-white rounded-lg shadow-xl w-full max-w-lg p-5" onClick={(ev) => ev.stopPropagation()}>
            <h2 className="text-lg font-semibold mb-3">{editing ? 'Edit hostname' : 'Add hostname'}</h2>
            <div className="space-y-3">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={form.enabled}
                  onChange={(ev) => setForm({ ...form, enabled: ev.target.checked })} />
                Enabled
              </label>
              <Field label="Provider">
                <select className="input" value={form.provider}
                  onChange={(ev) => setForm({ ...form, provider: ev.target.value, server: presets[ev.target.value]?.server ?? '' })}>
                  {Object.entries(presets).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}
                </select>
              </Field>
              <Field label="Hostname" hint="The FQDN to keep updated, e.g. vpn.example.com.">
                <input className="input" value={form.hostname} placeholder="vpn.example.com"
                  onChange={(ev) => setForm({ ...form, hostname: ev.target.value })} />
              </Field>
              {isCustom ? (
                <Field label="Update URL" hint="Use {ip} and {hostname} as placeholders.">
                  <input className="input" value={form.custom_url ?? ''} placeholder="https://www.duckdns.org/update?domains={hostname}&token=...&ip={ip}"
                    onChange={(ev) => setForm({ ...form, custom_url: ev.target.value })} />
                </Field>
              ) : (
                <>
                  <Field label="Server" hint="Pre-filled from the provider; override if needed.">
                    <input className="input" value={form.server}
                      onChange={(ev) => setForm({ ...form, server: ev.target.value })} />
                  </Field>
                  <Field label="Username">
                    <input className="input" value={form.username ?? ''}
                      onChange={(ev) => setForm({ ...form, username: ev.target.value })} />
                  </Field>
                  <Field label="Password / token" hint={editing ? 'Leave blank to keep the stored secret.' : undefined}>
                    <input type="password" className="input" value={form.password ?? ''}
                      onChange={(ev) => setForm({ ...form, password: ev.target.value })} />
                  </Field>
                </>
              )}
            </div>
            <div className="flex justify-end gap-2 mt-5">
              <button className="btn-secondary" onClick={closeForm} disabled={busy}>Cancel</button>
              <button className="btn-primary" onClick={save} disabled={busy || !form.hostname}>
                {busy ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}
      <ConfirmHost />
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
