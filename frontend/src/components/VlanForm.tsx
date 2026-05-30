import { useEffect, useState } from 'react'
import { Interface } from '../lib/api'
import Toggle from './Toggle'

type Props = {
  vlan?: Interface
  interfaces: Interface[]  // used to offer parents (existing physical interfaces)
  onSubmit: (data: Partial<Interface>) => Promise<void>
  onCancel: () => void
}

export default function VlanForm({ vlan, interfaces, onSubmit, onCancel }: Props) {
  // Attaching to a zone is done from the Zones page (nftables filtering
  // side), not here.
  const [parentInterface, setParentInterface] = useState(vlan?.parent_interface || '')
  const [vlanId, setVlanId] = useState<string>(vlan?.vlan_id ? String(vlan.vlan_id) : '')
  const [description, setDescription] = useState(vlan?.description || '')
  const [ipMode, setIpMode] = useState<Interface['ip_mode']>(vlan?.ip_mode || 'none')
  const [ipAddress, setIpAddress] = useState(vlan?.ip_address || '')
  const [gateway, setGateway] = useState(vlan?.gateway || '')
  const [dnsServers, setDnsServers] = useState(vlan?.dns_servers || '')
  const [mtu, setMtu] = useState<string>(vlan?.mtu ? String(vlan.mtu) : '')
  const [enabled, setEnabled] = useState(vlan?.enabled ?? true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Interfaces usable as parent: the non-VLAN ones
  const physicalParents = interfaces.filter((i) => i.type !== 'vlan')

  useEffect(() => {
    setParentInterface(vlan?.parent_interface || '')
    setVlanId(vlan?.vlan_id ? String(vlan.vlan_id) : '')
    setDescription(vlan?.description || '')
    setIpMode(vlan?.ip_mode || 'none')
    setIpAddress(vlan?.ip_address || '')
    setGateway(vlan?.gateway || '')
    setDnsServers(vlan?.dns_servers || '')
    setMtu(vlan?.mtu ? String(vlan.mtu) : '')
    setEnabled(vlan?.enabled ?? true)
  }, [vlan])

  // Name auto : <parent>.<vlan_id>
  const computedName = parentInterface && vlanId
    ? `${parentInterface.trim()}.${vlanId.trim()}`
    : ''

  const handleSubmit = async () => {
    if (!parentInterface.trim() || !vlanId.trim()) {
      setError("Parent interface and VLAN ID are required.")
      return
    }
    setSubmitting(true); setError(null)
    try {
      await onSubmit({
        name: vlan?.name || computedName,
        description: description.trim() || null,
        type: 'vlan',
        parent_interface: parentInterface.trim(),
        vlan_id: Number(vlanId),
        ip_mode: ipMode,
        ip_address: ipMode === 'static' ? (ipAddress.trim() || null) : null,
        gateway: ipMode === 'static' ? (gateway.trim() || null) : null,
        dns_servers: ipMode !== 'none' ? (dnsServers.trim() || null) : null,
        mtu: mtu ? Number(mtu) : null,
        enabled,
      })
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

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">Parent interface</label>
          <select
            className="select font-mono"
            value={parentInterface}
            onChange={(e) => setParentInterface(e.target.value)}
            disabled={!!vlan}
          >
            <option value="">choose...</option>
            {physicalParents.map((i) => (
              <option key={i.id} value={i.name}>{i.name}</option>
            ))}
          </select>
          {!vlan && physicalParents.length === 0 && (
            <p className="text-xs text-amber-800 mt-1">
              No physical interface registered. Import one before creating a VLAN.
            </p>
          )}
        </div>
        <div>
          <label className="label">ID VLAN (1-4094)</label>
          <input
            type="number"
            min={1}
            max={4094}
            className="input font-mono"
            placeholder="100"
            value={vlanId}
            onChange={(e) => setVlanId(e.target.value)}
            disabled={!!vlan}
          />
        </div>
      </div>

      <div className="bg-gray-50 border border-gray-200 rounded px-3 py-2 text-xs text-gray-700">
        <div className="uppercase tracking-wider mb-1">VLAN interface name</div>
        <code className="font-mono text-gray-900">
          {computedName || (vlan ? vlan.name : 'parent.vlan_id')}
        </code>
        <div className="mt-1">
          Kernel command: <code className="font-mono">ip link add link {parentInterface || '<parent>'} name {computedName || '<name>'} type vlan id {vlanId || '<id>'}</code>
        </div>
      </div>

      <div>
        <label className="label">Description</label>
        <input
          className="input"
          placeholder="Short description (e.g. prod, voip, dmz)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>

      <div className="border-t border-gray-200 pt-4 mt-2">
        <div className="text-xs uppercase tracking-wider text-gray-700 mb-3">VLAN IP configuration</div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">Mode</label>
            <select className="select" value={ipMode} onChange={(e) => setIpMode(e.target.value as Interface['ip_mode'])}>
              <option value="none">none (interface not configured)</option>
              <option value="static">static</option>
              <option value="dhcp">dhcp</option>
            </select>
          </div>
          <div>
            <label className="label">MTU</label>
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
              <label className="label">Address / CIDR</label>
              <input
                className="input font-mono"
                placeholder="10.0.100.1/24"
                value={ipAddress}
                onChange={(e) => setIpAddress(e.target.value)}
              />
            </div>
            <div>
              <label className="label">Gateway</label>
              <input
                className="input font-mono"
                placeholder="10.0.100.254 (optional)"
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
          VLAN enabled
        </label>
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <button className="btn-secondary" onClick={onCancel} disabled={submitting}>Cancel</button>
        <button
          className="btn-primary"
          onClick={handleSubmit}
          disabled={submitting || !parentInterface || !vlanId}
        >
          {submitting ? 'Saving...' : 'Save'}
        </button>
      </div>
    </div>
  )
}
