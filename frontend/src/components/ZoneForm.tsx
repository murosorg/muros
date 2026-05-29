import { useEffect, useState } from 'react'
import { api, Interface, Zone } from '../lib/api'

type Props = {
  zone?: Zone
  interfaces: Interface[]
  onSubmit: (data: Partial<Zone>) => Promise<void>
  onChange?: () => void  // called after an attachment update
  onCancel: () => void
}

export default function ZoneForm({ zone, interfaces, onSubmit, onChange, onCancel }: Props) {
  const [name, setName] = useState(zone?.name || '')
  const [description, setDescription] = useState(zone?.description || '')
  // Set of interface_ids attached to this zone (initial)
  const [attached, setAttached] = useState<Set<number>>(
    () => new Set(zone ? interfaces.filter((i) => i.zone_id === zone.id).map((i) => i.id) : []),
  )
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setName(zone?.name || '')
    setDescription(zone?.description || '')
    setAttached(new Set(zone ? interfaces.filter((i) => i.zone_id === zone.id).map((i) => i.id) : []))
  }, [zone, interfaces])

  const toggle = (id: number) => {
    setAttached((cur) => {
      const next = new Set(cur)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleSubmit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      // 1. Create or update the zone
      await onSubmit({ name: name.trim(), description: description.trim() || null })

      // 2. Sync the attachments (only if the zone already exists, or
      // if we are creating one; in the latter case the parent will
      // reload and the attachment will happen on the next edit).
      if (zone) {
        const initial = new Set(interfaces.filter((i) => i.zone_id === zone.id).map((i) => i.id))
        // Detach: interfaces that were in the zone but no longer in attached
        for (const i of interfaces) {
          const wasIn = initial.has(i.id)
          const isIn = attached.has(i.id)
          if (wasIn && !isIn) {
            await api.interfaces.update(i.id, { zone_id: null })
          } else if (!wasIn && isIn) {
            await api.interfaces.update(i.id, { zone_id: zone.id })
          }
        }
        onChange?.()
      }
    } catch (e) {
      setError(String(e))
    } finally { setSubmitting(false) }
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 px-3 py-2 rounded">
          {error}
        </div>
      )}
      <div>
        <label className="label">Zone name</label>
        <input
          className="input font-mono"
          placeholder="ex: dmz, wan, lan, mgmt"
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
        />
      </div>
      <div>
        <label className="label">Description</label>
        <input
          className="input"
          placeholder="Short description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>

      {zone && (
        <div>
          <label className="label">Attached interfaces</label>
          <p className="text-xs text-gray-700 mb-2">
            An interface can only belong to one zone. Checking here
            moves it into this zone (and detaches from the previous one if needed).
          </p>
          <div className="border border-gray-200 rounded max-h-64 overflow-auto">
            {interfaces.length === 0 && (
              <div className="px-3 py-4 text-sm text-gray-700">No interface registered.</div>
            )}
            {interfaces.map((i) => {
              const inOther = i.zone_id != null && i.zone_id !== zone.id
              return (
                <label
                  key={i.id}
                  className="flex items-center gap-2 px-3 py-2 border-b border-gray-100 last:border-b-0 hover:bg-gray-50 cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={attached.has(i.id)}
                    onChange={() => toggle(i.id)}
                  />
                  <span className="font-mono text-sm flex-1">{i.name}</span>
                  {i.type === 'vlan' && (
                    <span className="text-[10px] uppercase tracking-wider bg-gray-100 text-gray-700 border border-gray-200 px-1.5 py-0.5 rounded">vlan</span>
                  )}
                  {inOther && !attached.has(i.id) && (
                    <span className="text-[11px] text-amber-700">attached to another zone</span>
                  )}
                </label>
              )
            })}
          </div>
        </div>
      )}

      {!zone && (
        <p className="text-xs text-gray-700">
          You can attach interfaces to this zone after it is created.
        </p>
      )}

      <div className="flex justify-end gap-2 pt-2">
        <button className="btn-secondary" onClick={onCancel} disabled={submitting}>Cancel</button>
        <button className="btn-primary" onClick={handleSubmit} disabled={submitting || !name.trim()}>
          {submitting ? 'Saving...' : 'Save'}
        </button>
      </div>
    </div>
  )
}
