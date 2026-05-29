import { useEffect, useState } from 'react'
import { NatRule, Interface } from '../lib/api'
import HelpTooltip from './HelpTooltip'
import Toggle from './Toggle'

type Props = {
  rule?: NatRule
  interfaces: Interface[]
  // Type pre-selected on a fresh form. Used when "Add NAT rule" is
  // pressed from a hook tab so the form opens on the right kind
  // (masquerade for postrouting, dnat for prerouting).
  defaultType?: NatRule['type']
  onSubmit: (data: Partial<NatRule>) => Promise<void>
  onCancel: () => void
}

const empty: Partial<NatRule> = {
  position: 0,
  type: 'masquerade',
  interface_id: null,
  src_address: null,
  dst_address: null,
  protocol: null,
  dst_port: null,
  redirect_to_ip: null,
  redirect_to_port: null,
  enabled: true,
  comment: null,
}

export default function NatForm({ rule, interfaces, defaultType, onSubmit, onCancel }: Props) {
  const initial: Partial<NatRule> = rule
    ? rule
    : defaultType
      ? { ...empty, type: defaultType }
      : empty
  const [data, setData] = useState<Partial<NatRule>>(initial)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setData(rule ? rule : defaultType ? { ...empty, type: defaultType } : empty)
  }, [rule, defaultType])

  const set = <K extends keyof NatRule>(k: K, v: NatRule[K] | null) =>
    setData((d) => ({ ...d, [k]: v }))

  const handleSubmit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      const payload = { ...data }
      for (const k of ['src_address', 'dst_address', 'dst_port', 'redirect_to_ip', 'redirect_to_port', 'comment'] as const) {
        if (payload[k] === '') payload[k] = null
      }
      if (payload.protocol === 'any' || !payload.protocol) payload.protocol = null
      await onSubmit(payload)
    } catch (e) {
      setError(String(e))
    } finally {
      setSubmitting(false)
    }
  }

  const type = data.type || 'masquerade'
  const isMasq = type === 'masquerade'
  const isSnat = type === 'snat'
  const isDnat = type === 'dnat'

  return (
    <div className="space-y-4">
      {error && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 px-3 py-2 rounded">
          {error}
        </div>
      )}

      <div className="bg-slate-50 border border-slate-200 rounded p-3 text-xs">
        <div className="font-semibold text-gray-800 mb-1.5">What this rule will do</div>
        {isMasq && (
          <div className="font-mono text-gray-700 leading-relaxed">
            <div>LAN client <span className="text-blue-700">10.0.0.5</span> sends to Internet</div>
            <div className="ml-4">↓ packet leaves via the output interface</div>
            <div>Firewall rewrites source IP → <span className="text-amber-700">interface IP</span> (e.g. WAN IP from DHCP)</div>
            <div className="text-gray-600 mt-1">Use case: typical home/SMB router, one public IP, many private LAN hosts.</div>
          </div>
        )}
        {isSnat && (
          <div className="font-mono text-gray-700 leading-relaxed">
            <div>LAN client <span className="text-blue-700">10.0.0.5</span> sends to Internet</div>
            <div className="ml-4">↓ packet leaves via the output interface</div>
            <div>Firewall rewrites source IP → <span className="text-amber-700">fixed IP you choose</span></div>
            <div className="text-gray-600 mt-1">Use case: multiple public IPs and you want a specific source IP for a given LAN range.</div>
          </div>
        )}
        {isDnat && (
          <div className="font-mono text-gray-700 leading-relaxed">
            <div>External client → <span className="text-amber-700">WAN IP:8080</span></div>
            <div className="ml-4">↓ firewall rewrites destination</div>
            <div>Internal host <span className="text-blue-700">192.168.1.50:8080</span> receives the packet</div>
            <div className="text-gray-600 mt-1">Use case: port forwarding to publish an internal service (web, SSH, ...) on the WAN side.</div>
          </div>
        )}
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div>
          <label className="label">Position</label>
          <input
            type="number"
            className="input"
            value={data.position ?? 0}
            onChange={(e) => set('position', Number(e.target.value))}
          />
        </div>
        <div className="col-span-2">
          <label className="label">NAT type <HelpTooltip text="masquerade: auto SNAT to the egress interface IP (typical LAN -> WAN case). snat: source translation to a fixed IP (useful for multi-IP). dnat: destination redirection, e.g. publish an internal service on a WAN port." /></label>
          <select
            className="select"
            value={type}
            onChange={(e) => set('type', e.target.value as NatRule['type'])}
          >
            <option value="masquerade">masquerade (auto SNAT to the output interface IP)</option>
            <option value="snat">snat (source translation to a fixed IP)</option>
            <option value="dnat">dnat (port forwarding to an internal host)</option>
          </select>
        </div>
      </div>

      <div>
        <label className="label">
          {isDnat ? "Input interface (leave empty for all)" : 'Output interface'}
        </label>
        <select
          className="select"
          value={data.interface_id ?? ''}
          onChange={(e) => set('interface_id', e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">{isDnat ? 'any' : '-- choose --'}</option>
          {interfaces.map((i) => (
            <option key={i.id} value={i.id}>{i.name}{i.description ? ` (${i.description})` : ''}</option>
          ))}
        </select>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">Source address</label>
          <input
            className="input font-mono"
            placeholder={isDnat ? 'optional' : 'e.g. 192.168.0.0/16'}
            value={data.src_address ?? ''}
            onChange={(e) => set('src_address', e.target.value)}
          />
        </div>
        <div>
          <label className="label">Destination address {isDnat && '(public IP received)'}</label>
          <input
            className="input font-mono"
            placeholder={isDnat ? 'e.g. 203.0.113.10' : 'optional'}
            value={data.dst_address ?? ''}
            onChange={(e) => set('dst_address', e.target.value)}
          />
        </div>
      </div>

      {isDnat && (
        <>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="label">Protocol</label>
              <select
                className="select"
                value={data.protocol ?? 'tcp'}
                onChange={(e) => set('protocol', e.target.value as NatRule['protocol'])}
              >
                <option value="tcp">tcp</option>
                <option value="udp">udp</option>
              </select>
            </div>
            <div>
              <label className="label">External port</label>
              <input
                className="input font-mono"
                placeholder="e.g. 8080"
                value={data.dst_port ?? ''}
                onChange={(e) => set('dst_port', e.target.value)}
              />
            </div>
            <div>
              <label className="label">
                Internal port
                <HelpTooltip text="The internal port to redirect to. Empty = same as External port (1-to-1 publishing). Different if you re-badge: e.g. External port 443 -> Internal port 8443 if the internal app listens on 8443." />
              </label>
              <input
                className="input font-mono"
                placeholder="leave empty = same"
                value={data.redirect_to_port ?? ''}
                onChange={(e) => set('redirect_to_port', e.target.value)}
              />
            </div>
          </div>
          <div>
            <label className="label">Redirect to internal IP</label>
            <input
              className="input font-mono"
              placeholder="e.g. 192.168.1.50"
              value={data.redirect_to_ip ?? ''}
              onChange={(e) => set('redirect_to_ip', e.target.value)}
            />
          </div>
        </>
      )}

      {isSnat && (
        <div>
          <label className="label">Source IP substitute</label>
          <input
            className="input font-mono"
            placeholder="e.g. 203.0.113.10"
            value={data.redirect_to_ip ?? ''}
            onChange={(e) => set('redirect_to_ip', e.target.value)}
          />
        </div>
      )}

      <div>
        <label className="label">Comment</label>
        <input
          className="input"
          placeholder="Short description"
          value={data.comment ?? ''}
          onChange={(e) => set('comment', e.target.value)}
        />
      </div>

      <div className="flex items-center gap-6 pt-1">
        <label className="flex items-center gap-2 text-sm">
          <Toggle checked={data.enabled ?? true} onChange={(v) => set('enabled', v)} />
          Enabled
        </label>
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <button className="btn-secondary" onClick={onCancel} disabled={submitting}>Cancel</button>
        <button className="btn-primary" onClick={handleSubmit} disabled={submitting}>
          {submitting ? 'Saving...' : 'Save'}
        </button>
      </div>
    </div>
  )
}
