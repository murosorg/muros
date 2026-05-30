import { useEffect, useState } from 'react'
import { Interface } from '../lib/api'
import CidrInput from './CidrInput'
import HelpTooltip from './HelpTooltip'
import Toggle from './Toggle'

type Props = {
  iface?: Interface
  defaultName?: string
  onSubmit: (data: Partial<Interface>) => Promise<void>
  onCancel: () => void
}

export default function InterfaceForm({ iface, defaultName, onSubmit, onCancel }: Props) {
  // This form only handles physical interfaces.
  // VLANs have their own form (VlanForm.tsx).
  // Attaching to a zone is done from the Zones page (nftables filtering
  // side), not here.
  const [name, setName] = useState(iface?.name || defaultName || '')
  const [description, setDescription] = useState(iface?.description || '')
  const [ipMode, setIpMode] = useState<Interface['ip_mode']>(iface?.ip_mode || 'none')
  const [ipAddress, setIpAddress] = useState(iface?.ip_address || '')
  const [gateway, setGateway] = useState(iface?.gateway || '')
  const [dnsServers, setDnsServers] = useState(iface?.dns_servers || '')
  const [mtu, setMtu] = useState<string>(iface?.mtu ? String(iface.mtu) : '')
  const [enabled, setEnabled] = useState(iface?.enabled ?? true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setName(iface?.name || defaultName || '')
    setDescription(iface?.description || '')
    setIpMode(iface?.ip_mode || 'none')
    setIpAddress(iface?.ip_address || '')
    setGateway(iface?.gateway || '')
    setDnsServers(iface?.dns_servers || '')
    setMtu(iface?.mtu ? String(iface.mtu) : '')
    setEnabled(iface?.enabled ?? true)
  }, [iface, defaultName])

  const handleSubmit = async () => {
    setSubmitting(true)
    setError(null)
    // Client-side validation: reject an IP without a prefix (otherwise /32 = lockout)
    if (ipMode === 'static' && ipAddress.trim()) {
      const v = ipAddress.trim()
      if (!v.includes('/')) {
        setError("Set the CIDR mask, e.g. 192.168.1.70/24. Without a prefix, the interface would be isolated from its LAN.")
        setSubmitting(false)
        return
      }
      const m = v.match(/\/(\d+)$/)
      if (m) {
        const p = parseInt(m[1], 10)
        const isV6 = v.includes(':')
        if (!isV6 && p === 32) {
          setError("Prefix /32 refused: would isolate the interface from its LAN. Use the real mask (e.g. /24).")
          setSubmitting(false); return
        }
        if (isV6 && p === 128) {
          setError("Prefix /128 refused: would isolate the interface from its IPv6 LAN.")
          setSubmitting(false); return
        }
      }
    }
    try {
      await onSubmit({
        name: name.trim(),
        description: description.trim() || null,
        type: 'physical',
        parent_interface: null,
        vlan_id: null,
        ip_mode: ipMode,
        ip_address: ipMode === 'static' ? (ipAddress.trim() || null) : null,
        gateway: ipMode === 'static' ? (gateway.trim() || null) : null,
        dns_servers: ipMode !== 'none' ? (dnsServers.trim() || null) : null,
        mtu: mtu ? Number(mtu) : null,
        enabled,
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
        <label className="label">Interface name</label>
        <input
          className="input font-mono"
          placeholder="ex: eth0, enp3s0, wlan0"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={!!iface}
          autoFocus={!iface}
        />
        {iface && (
          <p className="text-xs text-gray-700 mt-1">The name cannot be changed. Delete and recreate the interface if needed.</p>
        )}
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

      <div className="border-t border-gray-200 pt-4 mt-2">
        <div className="text-xs uppercase tracking-wider text-gray-700 mb-3">IP configuration</div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">Mode</label>
            <select
              className="select"
              value={ipMode}
              onChange={(e) => setIpMode(e.target.value as Interface['ip_mode'])}
            >
              <option value="none">none (interface not configured)</option>
              <option value="static">static</option>
              <option value="dhcp">dhcp</option>
            </select>
          </div>
          <div>
            <label className="label">MTU <HelpTooltip text="Maximum Transmission Unit in bytes. Default 1500 on Ethernet. Lower to 1492 behind PPPoE, 1280-1380 on WireGuard/IPsec if fragmentation is observed." /></label>
            <input
              type="number"
              className="input font-mono"
              placeholder="default: 1500"
              value={mtu}
              onChange={(e) => setMtu(e.target.value)}
            />
          </div>
        </div>

        {ipMode === 'static' && (
          <div className="grid grid-cols-2 gap-3 mt-3">
            <div>
              <label className="label">IP address / mask</label>
              <CidrInput
                value={ipAddress}
                onChange={setIpAddress}
                placeholder="192.168.1.1"
              />
            </div>
            <div>
              <label className="label">Gateway</label>
              <input
                className="input font-mono"
                placeholder="192.168.1.254 (optional)"
                value={gateway}
                onChange={(e) => setGateway(e.target.value)}
              />
            </div>
          </div>
        )}

        {ipMode !== 'none' && (
          <div className="mt-3">
            <label className="label">DNS servers</label>
            <input
              className="input font-mono"
              placeholder="ex: 1.1.1.1, 8.8.8.8"
              value={dnsServers}
              onChange={(e) => setDnsServers(e.target.value)}
            />
          </div>
        )}
      </div>

      <div className="flex items-center gap-6 pt-1">
        <label className="flex items-center gap-2 text-sm">
          <Toggle checked={enabled} onChange={setEnabled} />
          Interface enabled
        </label>
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <button className="btn-secondary" onClick={onCancel} disabled={submitting}>Cancel</button>
        <button className="btn-primary" onClick={handleSubmit} disabled={submitting || !name.trim()}>
          {submitting ? 'Saving...' : 'Save'}
        </button>
      </div>
    </div>
  )
}
