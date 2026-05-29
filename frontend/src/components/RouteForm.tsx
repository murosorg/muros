import { useEffect, useState } from 'react'
import { Interface, StaticRoute } from '../lib/api'
import Toggle from './Toggle'

type Props = {
  route?: StaticRoute
  interfaces: Interface[]
  onSubmit: (data: Partial<StaticRoute>) => Promise<void>
  onCancel: () => void
}

export default function RouteForm({ route, interfaces, onSubmit, onCancel }: Props) {
  const [destination, setDestination] = useState(route?.destination || '')
  const [gateway, setGateway] = useState(route?.gateway || '')
  const [interfaceId, setInterfaceId] = useState<number | null>(route?.interface_id ?? null)
  const [metric, setMetric] = useState<string>(route ? String(route.metric) : '0')
  const [enabled, setEnabled] = useState(route?.enabled ?? true)
  const [comment, setComment] = useState(route?.comment || '')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setDestination(route?.destination || '')
    setGateway(route?.gateway || '')
    setInterfaceId(route?.interface_id ?? null)
    setMetric(route ? String(route.metric) : '0')
    setEnabled(route?.enabled ?? true)
    setComment(route?.comment || '')
  }, [route])

  const handleSubmit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      await onSubmit({
        destination: destination.trim(),
        gateway: gateway.trim() || null,
        interface_id: interfaceId,
        metric: Number(metric) || 0,
        enabled,
        comment: comment.trim() || null,
      })
    } catch (e) {
      setError(String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 px-3 py-2 rounded">
          {error}
        </div>
      )}

      <div>
        <label className="label">Destination</label>
        <input
          className="input font-mono"
          placeholder="e.g. 10.0.0.0/8 or default"
          value={destination}
          onChange={(e) => setDestination(e.target.value)}
          autoFocus
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">Gateway</label>
          <input
            className="input font-mono"
            placeholder="e.g. 192.168.1.254"
            value={gateway}
            onChange={(e) => setGateway(e.target.value)}
          />
        </div>
        <div>
          <label className="label">Output interface</label>
          <select
            className="select"
            value={interfaceId ?? ''}
            onChange={(e) => setInterfaceId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">automatic</option>
            {interfaces.map((i) => (
              <option key={i.id} value={i.id}>{i.name}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">Metric</label>
          <input
            type="number"
            className="input font-mono"
            value={metric}
            onChange={(e) => setMetric(e.target.value)}
          />
        </div>
      </div>

      <div>
        <label className="label">Comment</label>
        <input
          className="input"
          placeholder="Short description"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
      </div>

      <div className="flex items-center gap-6 pt-1">
        <label className="flex items-center gap-2 text-sm">
          <Toggle checked={enabled} onChange={setEnabled} />
          Enabled
        </label>
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <button className="btn-secondary" onClick={onCancel} disabled={submitting}>Cancel</button>
        <button className="btn-primary" onClick={handleSubmit} disabled={submitting || !destination.trim()}>
          {submitting ? 'Saving...' : 'Save'}
        </button>
      </div>
    </div>
  )
}
