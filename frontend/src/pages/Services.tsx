import { useEffect, useState } from 'react'
import { api, type ServiceGroup, type ServiceGroupPort, type AddressGroup, type AddressGroupEntry } from '../lib/api'
import PageHeader from '../components/PageHeader'
import ApplyFirewallButton from '../components/ApplyFirewallButton'
import Modal from '../components/Modal'
import RulesetModal from '../components/RulesetModal'
import { ErrorBlock } from '../components/Alerts'
import EmptyState from '../components/EmptyState'
import { useConfirm } from '../components/ConfirmModal'
import { Layers } from 'lucide-react'

export default function ServicesPage() {
  const [serviceGroups, setServiceGroups] = useState<ServiceGroup[]>([])
  const [addressGroups, setAddressGroups] = useState<AddressGroup[]>([])
  const [editingService, setEditingService] = useState<ServiceGroup | null>(null)
  const [creatingService, setCreatingService] = useState(false)
  const [editingAddress, setEditingAddress] = useState<AddressGroup | null>(null)
  const [creatingAddress, setCreatingAddress] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showRuleset, setShowRuleset] = useState(false)
  const { confirm, ConfirmHost } = useConfirm()

  async function reloadServices() {
    try { setServiceGroups(await api.serviceGroups.list()); setError(null) }
    catch (e: unknown) { setError(e instanceof Error ? e.message : String(e)) }
  }
  async function reloadAddresses() {
    try { setAddressGroups(await api.addressGroups.list()); setError(null) }
    catch (e: unknown) { setError(e instanceof Error ? e.message : String(e)) }
  }
  useEffect(() => { void reloadServices(); void reloadAddresses() }, [])

  async function delService(g: ServiceGroup) {
    const ok = await confirm({
      title: `Delete le groupe "${g.name}" ?`,
      message: 'Filter rules referencing this group will lose their reference.',
      destructive: true,
    })
    if (!ok) return
    try { await api.serviceGroups.remove(g.id); void reloadServices() }
    catch (e: unknown) { setError(e instanceof Error ? e.message : String(e)) }
  }
  async function delAddress(g: AddressGroup) {
    const ok = await confirm({
      title: `Delete le groupe "${g.name}" ?`,
      message: 'Filter rules referencing this group will lose their reference.',
      destructive: true,
    })
    if (!ok) return
    try { await api.addressGroups.remove(g.id); void reloadAddresses() }
    catch (e: unknown) { setError(e instanceof Error ? e.message : String(e)) }
  }

  return (
    <div>
      <PageHeader
        icon={<Layers size={16} />}
       
        title="Services"
        description="Port and address groups reusable in rules."
        actions={<ApplyFirewallButton onClick={() => setShowRuleset(true)} onView={() => setShowRuleset(true)} />}
      />

      <ConfirmHost />
      {showRuleset && <RulesetModal onClose={() => setShowRuleset(false)} />}

      <div className="px-6 py-4 space-y-4">
        {error && (
          <ErrorBlock message={error} />
        )}

        {/* 2 colonnes sur lg+ (>= 1024px), empile sur petit ecran. */}
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
          <section>
            <div className="flex items-center justify-between mb-2 gap-2 flex-wrap">
              <h2 className="text-sm font-semibold text-gray-900">Port groups</h2>
              <button className="btn-primary" onClick={() => setCreatingService(true)}>Add</button>
            </div>
            <p className="text-xs text-gray-700 mb-2">
              Groups multiple protocol/port pairs (e.g. LDAP = tcp/389 + tcp/636).
              A rule can reference a group instead of re-typing the list.
            </p>
            <div className="border border-gray-200 rounded-md overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-gray-700 text-xs uppercase tracking-wider">
                  <tr>
                    <th className="text-left px-3 py-2 w-32">Name</th>
                    <th className="text-left px-3 py-2">Ports</th>
                    <th className="text-right px-3 py-2 w-28"></th>
                  </tr>
                </thead>
                <tbody>
                  {serviceGroups.length === 0 ? (
                    <tr><td colSpan={3}><EmptyState
                      text="No port group"
                      hint="Group multiple protocol/port pairs (e.g. LDAP, AD, Web)."
                      action={<button className="btn-primary" onClick={() => setCreatingService(true)}>Add a port group</button>}
                    /></td></tr>
                  ) : serviceGroups.map(g => (
                    <tr key={g.id} className="border-t border-gray-200 hover:bg-gray-50">
                      <td className="px-3 py-2 font-mono align-top">
                        <div>{g.name}</div>
                        {g.description && <div className="text-xs text-gray-600 mt-0.5">{g.description}</div>}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-800 align-top">
                        {g.ports.map(p => `${p.protocol}/${p.port}`).join(', ')}
                      </td>
                      <td className="px-3 py-2 text-right whitespace-nowrap align-top">
                        <button className="btn-ghost py-1" onClick={() => setEditingService(g)}>Edit</button>
                        <button className="btn-ghost py-1 text-red-700 hover:text-red-800" onClick={() => delService(g)}>Delete</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section>
            <div className="flex items-center justify-between mb-2 gap-2 flex-wrap">
              <h2 className="text-sm font-semibold text-gray-900">Address groups</h2>
              <button className="btn-primary" onClick={() => setCreatingAddress(true)}>Add</button>
            </div>
            <p className="text-xs text-gray-700 mb-2">
              Groups multiple IPs or CIDRs (e.g. admin LAN = 192.168.10.0/24 + 10.0.0.0/8).
              Injected as an nft set <code className="font-mono">ip saddr {'{ ... }'}</code> in the rules.
            </p>
            <div className="border border-gray-200 rounded-md overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-gray-700 text-xs uppercase tracking-wider">
                  <tr>
                    <th className="text-left px-3 py-2 w-32">Name</th>
                    <th className="text-left px-3 py-2">Addresses</th>
                    <th className="text-right px-3 py-2 w-28"></th>
                  </tr>
                </thead>
                <tbody>
                  {addressGroups.length === 0 ? (
                    <tr><td colSpan={3}><EmptyState
                      text="No address group"
                      hint="Group multiple IPs or CIDRs (e.g. admin LAN, VPN clients)."
                      action={<button className="btn-primary" onClick={() => setCreatingAddress(true)}>Add an address group</button>}
                    /></td></tr>
                  ) : addressGroups.map(g => (
                    <tr key={g.id} className="border-t border-gray-200 hover:bg-gray-50">
                      <td className="px-3 py-2 font-mono align-top">
                        <div>{g.name}</div>
                        {g.description && <div className="text-xs text-gray-600 mt-0.5">{g.description}</div>}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-800 align-top">
                        {g.entries.map(e => e.value).join(', ')}
                      </td>
                      <td className="px-3 py-2 text-right whitespace-nowrap align-top">
                        <button className="btn-ghost py-1" onClick={() => setEditingAddress(g)}>Edit</button>
                        <button className="btn-ghost py-1 text-red-700 hover:text-red-800" onClick={() => delAddress(g)}>Delete</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </div>

      {(creatingService || editingService) && (
        <ServiceGroupModal
          initial={editingService}
          onClose={() => { setCreatingService(false); setEditingService(null) }}
          onSaved={() => { setCreatingService(false); setEditingService(null); void reloadServices() }}
        />
      )}
      {(creatingAddress || editingAddress) && (
        <AddressGroupModal
          initial={editingAddress}
          onClose={() => { setCreatingAddress(false); setEditingAddress(null) }}
          onSaved={() => { setCreatingAddress(false); setEditingAddress(null); void reloadAddresses() }}
        />
      )}
    </div>
  )
}

function ServiceGroupModal({ initial, onClose, onSaved }: {
  initial: ServiceGroup | null
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(initial?.name || '')
  const [description, setDescription] = useState(initial?.description || '')
  const [ports, setPorts] = useState<ServiceGroupPort[]>(initial?.ports || [])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  function addPort() { setPorts([...ports, { protocol: 'tcp', port: '' }]) }
  function removePort(idx: number) { setPorts(ports.filter((_, i) => i !== idx)) }
  function updatePort(idx: number, p: Partial<ServiceGroupPort>) {
    setPorts(ports.map((x, i) => i === idx ? { ...x, ...p } : x))
  }

  async function submit() {
    setBusy(true); setErr(null)
    try {
      const cleaned = ports.filter(p => p.port.trim() !== '')
      const payload = { name, description: description || null, ports: cleaned }
      if (initial) await api.serviceGroups.update(initial.id, payload)
      else await api.serviceGroups.create(payload)
      onSaved()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally { setBusy(false) }
  }

  return (
    <Modal open title={initial ? `Edit ${initial.name}` : 'New port group'} onClose={onClose} size="lg">
      {err && <ErrorBlock message={err} />}
      <div className="space-y-3">
        <div>
          <label className="label">Name</label>
          <input className="input" value={name} onChange={e => setName(e.target.value)} placeholder="LDAP, AD, Web, ..." />
        </div>
        <div>
          <label className="label">Description (optional)</label>
          <input className="input" value={description} onChange={e => setDescription(e.target.value)} />
        </div>
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="label mb-0">Ports</label>
            <button className="btn-secondary text-xs" onClick={addPort}>Add a port</button>
          </div>
          {ports.length === 0 ? (
            <div className="text-sm text-gray-700 italic py-2">No port. Click "Add a port".</div>
          ) : (
            <div className="space-y-2">
              {ports.map((p, idx) => (
                <div key={idx} className="flex items-center gap-2">
                  <select
                    className="select w-24"
                    value={p.protocol}
                    onChange={e => updatePort(idx, { protocol: e.target.value as 'tcp' | 'udp' })}
                  >
                    <option value="tcp">tcp</option>
                    <option value="udp">udp</option>
                  </select>
                  <input
                    className="input flex-1 font-mono"
                    value={p.port}
                    onChange={e => updatePort(idx, { port: e.target.value })}
                    placeholder="389 or 1024-2048"
                  />
                  <button className="btn-ghost py-1 text-red-700 hover:text-red-800" onClick={() => removePort(idx)}>Retirer</button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="btn-primary" onClick={submit} disabled={busy}>
          {busy ? 'Saving...' : 'Save'}
        </button>
      </div>
    </Modal>
  )
}

function AddressGroupModal({ initial, onClose, onSaved }: {
  initial: AddressGroup | null
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(initial?.name || '')
  const [description, setDescription] = useState(initial?.description || '')
  const [entries, setEntries] = useState<AddressGroupEntry[]>(initial?.entries || [])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  function addEntry() { setEntries([...entries, { value: '' }]) }
  function removeEntry(idx: number) { setEntries(entries.filter((_, i) => i !== idx)) }
  function updateEntry(idx: number, value: string) {
    setEntries(entries.map((x, i) => i === idx ? { ...x, value } : x))
  }

  async function submit() {
    setBusy(true); setErr(null)
    try {
      const cleaned = entries.filter(e => e.value.trim() !== '').map(e => ({ value: e.value }))
      const payload = { name, description: description || null, entries: cleaned }
      if (initial) await api.addressGroups.update(initial.id, payload)
      else await api.addressGroups.create(payload)
      onSaved()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally { setBusy(false) }
  }

  return (
    <Modal open title={initial ? `Edit ${initial.name}` : 'New address group'} onClose={onClose} size="lg">
      {err && <ErrorBlock message={err} />}
      <div className="space-y-3">
        <div>
          <label className="label">Name</label>
          <input className="input" value={name} onChange={e => setName(e.target.value)} placeholder="LAN admin, VPN clients, ..." />
        </div>
        <div>
          <label className="label">Description (optional)</label>
          <input className="input" value={description} onChange={e => setDescription(e.target.value)} />
        </div>
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="label mb-0">Addresses (IP or CIDR)</label>
            <button className="btn-secondary text-xs" onClick={addEntry}>Add</button>
          </div>
          {entries.length === 0 ? (
            <div className="text-sm text-gray-700 italic py-2">No address. Click "Add".</div>
          ) : (
            <div className="space-y-2">
              {entries.map((e, idx) => (
                <div key={idx} className="flex items-center gap-2">
                  <input
                    className="input flex-1 font-mono"
                    value={e.value}
                    onChange={ev => updateEntry(idx, ev.target.value)}
                    placeholder="192.168.10.0/24 or 10.0.0.1"
                  />
                  <button className="btn-ghost py-1 text-red-700 hover:text-red-800" onClick={() => removeEntry(idx)}>Retirer</button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="btn-primary" onClick={submit} disabled={busy}>
          {busy ? 'Saving...' : 'Save'}
        </button>
      </div>
    </Modal>
  )
}
