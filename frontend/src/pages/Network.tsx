import { useEffect, useMemo, useState } from 'react'
import { api, Interface, SystemInterface, Zone } from '../lib/api'
import Modal from '../components/Modal'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'
import { ZoneBadge } from '../lib/zoneColor'
import { useConfirm } from '../components/ConfirmModal'
import NetworkEnvironmentWarning from '../components/NetworkEnvironmentWarning'
import InterfaceForm from '../components/InterfaceForm'
import VlanForm from '../components/VlanForm'
import Toggle from '../components/Toggle'
import ApplyNetworkButton from '../components/ApplyNetworkButton'
import TableSkeleton from '../components/TableSkeleton'
import { ErrorBlock } from '../components/Alerts'
import { Network as NetworkIcon } from 'lucide-react'

export default function Network() {
  const [interfaces, setInterfaces] = useState<Interface[]>([])
  const [systemIfs, setSystemIfs] = useState<SystemInterface[] | null>(null)
  const [zones, setZones] = useState<Zone[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const { confirm, ConfirmHost } = useConfirm()

  const [creatingIface, setCreatingIface] = useState(false)
  const [editingIface, setEditingIface] = useState<Interface | null>(null)
  const [deletingIface, setDeletingIface] = useState<Interface | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [importingName, setImportingName] = useState<string | null>(null)

  const [creatingVlan, setCreatingVlan] = useState(false)
  const [editingVlan, setEditingVlan] = useState<Interface | null>(null)
  const [deletingVlan, setDeletingVlan] = useState<Interface | null>(null)

  const zoneById = useMemo(() => {
    const m = new Map<number, Zone>()
    zones.forEach((z) => m.set(z.id, z))
    return m
  }, [zones])

  const matchesSearch = (i: Interface): boolean => {
    if (!search) return true
    const q = search.toLowerCase()
    const zone = i.zone_id ? zoneById.get(i.zone_id)?.name?.toLowerCase() : ''
    return (
      i.name.toLowerCase().includes(q) ||
      (i.description || '').toLowerCase().includes(q) ||
      (i.ip_address || '').toLowerCase().includes(q) ||
      (i.parent_interface || '').toLowerCase().includes(q) ||
      (zone || '').includes(q)
    )
  }

  const physicalInterfaces = useMemo(
    () => interfaces.filter((i) => i.type !== 'vlan' && matchesSearch(i)),
    [interfaces, search, zoneById]
  )
  // Hide the description column entirely when no physical interface has a
  // description filled in. Keeps the table tighter on fresh installs while
  // still surfacing the column the moment an admin starts annotating
  // interfaces.
  const showPhysicalDescription = useMemo(
    () => physicalInterfaces.some((i) => (i.description || '').trim() !== ''),
    [physicalInterfaces]
  )
  const vlanInterfaces = useMemo(
    () => interfaces.filter((i) => i.type === 'vlan' && matchesSearch(i)),
    [interfaces, search, zoneById]
  )
  const knownNames = useMemo(() => new Set(interfaces.map((i) => i.name)), [interfaces])

  const reload = async () => {
    setLoading(true)
    try {
      // On charge en parallele les interfaces DB et l'etat noyau. Permet
      // de detecter qu'une iface en DB est en ip_mode='none' alors que
      // le noyau a une IP vivante (heritage du DHCP a l'install) :
      // l'admin doit pouvoir "figer" cette IP en un clic avant de
      // re-applique quoi que ce soit, sinon il se lockout au reboot.
      const [i, sys, z] = await Promise.all([
        api.interfaces.list(),
        api.interfaces.listSystem().catch(() => [] as SystemInterface[]),
        api.zones.list().catch(() => [] as Zone[]),
      ])
      setInterfaces(i)
      setSystemIfs(sys)
      setZones(z)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally { setLoading(false) }
  }

  // Lookup of live IPs by interface name, filtered to global IPv4.
  const liveIpByName = useMemo(() => {
    const m = new Map<string, string>()
    for (const s of (systemIfs || [])) {
      for (const cidr of s.addresses) {
        const ip = cidr.split('/', 1)[0]
        if (ip.includes(':')) continue
        if (ip.startsWith('169.254.') || ip.startsWith('127.')) continue
        m.set(s.name, cidr)
        break
      }
    }
    return m
  }, [systemIfs])

  // Lookup of live default gateways by interface name. Used as a hint
  // when the DB gateway is empty but the kernel has one (e.g. fresh
  // DHCP install before the admin pins the config).
  const liveGwByName = useMemo(() => {
    const m = new Map<string, string>()
    for (const s of (systemIfs || [])) {
      if (s.gateway) m.set(s.name, s.gateway)
    }
    return m
  }, [systemIfs])

  const importIp = async (i: Interface) => {
    const ok = await confirm({
      title: `Pin IP for ${i.name}?`,
      message: `IP ${liveIpByName.get(i.name)} and the current gateway will be pinned in static mode. The IP stays applied in the kernel, we just persist the config in the MurOS DB. No network outage.`,
    })
    if (!ok) return
    try {
      await api.interfaces.importCurrent(i.id)
      await reload()
    } catch (e) { setError((e as Error).message) }
  }

  const toggleIfaceEnabled = async (i: Interface) => {
    try {
      await api.interfaces.update(i.id, { enabled: !i.enabled })
      await reload()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const openImport = () => {
    // systemIfs are already loaded at mount via reload(), no refetch
    // needed here.
    setImportOpen(true)
  }

  useEffect(() => { reload() }, [])

  return (
    <div>
      <ConfirmHost />
      <PageHeader
        icon={<NetworkIcon size={16} />}
       
        title="Network"
        description="Physical interfaces and VLANs."
        actions={<ApplyNetworkButton />}
      />

      <div className="px-6 py-4 space-y-6">
        <NetworkEnvironmentWarning />
        {error && (
          <ErrorBlock message={error} />
        )}

        <section>
          <div className="flex items-center justify-between mb-2 gap-3 flex-wrap">
            <h2 className="text-sm font-semibold text-gray-900">Physical interfaces</h2>
            <input
              className="input flex-1 min-w-[200px] max-w-md py-1.5"
              placeholder="Filter by name, IP, description, zone..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <div className="flex gap-2">
              <button className="btn-secondary" onClick={openImport}>Import from system</button>
              <button className="btn-primary" onClick={() => setCreatingIface(true)}>Add an interface</button>
            </div>
          </div>
          <div className="border border-gray-200 rounded-md overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-700 text-xs uppercase tracking-wider">
                <tr>
                  <th className="text-center px-3 py-2 w-16">Enabled</th>
                  <th className="text-left px-3 py-2 w-12">ID</th>
                  <th className="text-left px-3 py-2 w-40">Name</th>
                  <th className="text-left px-3 py-2 w-32">Zone</th>
                  <th className="text-left px-3 py-2 w-24">IP mode</th>
                  <th className="text-left px-3 py-2 w-44">Address</th>
                  <th className="text-left px-3 py-2 w-40">Gateway</th>
                  {showPhysicalDescription && (
                    <th className="text-left px-3 py-2">Description</th>
                  )}
                  <th className="text-right px-3 py-2 w-28"></th>
                </tr>
              </thead>
              <tbody>
                {loading && <TableSkeleton rows={5} cols={9} />}
                {!loading && physicalInterfaces.length === 0 && (
                  <tr><td colSpan={showPhysicalDescription ? 9 : 8}>
                    <EmptyState
                      icon={<NetworkIcon size={20} />}
                      text={search ? 'No interface matches the filter' : 'No physical interface'}
                      hint={search ? undefined : 'MurOS adopts physical interfaces detected at install. Click Import from system to attach a new one.'}
                    />
                  </td></tr>
                )}
                {physicalInterfaces.map((i) => {
                  const liveIp = liveIpByName.get(i.name)
                  // Critical drift: DB says "no IP" but the kernel has one
                  // (DHCP inheritance from install). Applying as-is drops the IP.
                  const driftRisky = i.ip_mode === 'none' && !!liveIp
                  const zone = i.zone_id ? zoneById.get(i.zone_id) : null
                  return (
                    <tr key={i.id} className={`border-t border-gray-200 hover:bg-gray-50 ${driftRisky ? 'bg-amber-50/40' : !i.enabled ? 'bg-gray-50' : ''}`}>
                      <td className="px-3 py-2 text-center">
                        <Toggle size="sm" checked={i.enabled} onChange={() => toggleIfaceEnabled(i)} />
                      </td>
                      <td className={`px-3 py-2 font-mono text-gray-700 ${!i.enabled ? 'opacity-50' : ''}`}>{i.id}</td>
                      <td className={`px-3 py-2 font-mono ${!i.enabled ? 'opacity-50' : ''}`}>{i.name}</td>
                      <td className={`px-3 py-2 ${!i.enabled ? 'opacity-50' : ''}`}>
                        {zone ? <ZoneBadge name={zone.name} /> : <span className="text-xs text-gray-400">unzoned</span>}
                      </td>
                      <td className={`px-3 py-2 font-mono text-xs text-gray-800 ${!i.enabled ? 'opacity-50' : ''}`}>{i.ip_mode}</td>
                      <td className={`px-3 py-2 font-mono text-xs ${!i.enabled ? 'opacity-50' : ''}`}>
                        {i.ip_mode === 'dhcp' ? <span className="text-gray-600">dynamic</span>
                          : i.ip_address || <span className="text-gray-600">not configured</span>}
                        {driftRisky && (
                          <div className="mt-1 text-[11px] text-amber-900 leading-snug font-sans">
                            Live kernel IP: <span className="font-mono">{liveIp}</span>.
                            Click <strong>Import</strong> to pin this config in the DB
                            before the next reboot (otherwise the IP will be lost).
                          </div>
                        )}
                      </td>
                      <td className={`px-3 py-2 font-mono text-xs ${!i.enabled ? 'opacity-50' : ''}`}>
                        {i.gateway
                          ? <span className="text-gray-800">{i.gateway}</span>
                          : liveGwByName.get(i.name)
                            ? (
                              <span title="Default gateway seen on the kernel, not yet pinned in the MurOS DB.">
                                <span className="text-gray-800">{liveGwByName.get(i.name)}</span>
                                <span className="ml-1 text-[10px] text-amber-800">(live)</span>
                              </span>
                            )
                            : <span className="text-gray-500">-</span>}
                      </td>
                      {showPhysicalDescription && (
                        <td className={`px-3 py-2 text-gray-800 ${!i.enabled ? 'opacity-50' : ''}`}>
                          {i.description
                            ? i.description
                            : <span className="text-xs text-gray-400 italic">Add a description in Edit</span>}
                        </td>
                      )}
                      <td className="px-3 py-2 text-right whitespace-nowrap">
                        {driftRisky && (
                          <button
                            className="btn-secondary py-0.5 px-2 text-xs mr-1"
                            onClick={() => importIp(i)}
                            title="Reads the current kernel IP and gateway, stores them in static mode in the MurOS DB. No outage."
                          >
                            Import IP
                          </button>
                        )}
                        <button className="btn-ghost py-1" onClick={() => setEditingIface(i)}>Edit</button>
                        <button className="btn-ghost py-1 text-red-700 hover:text-red-800" onClick={() => setDeletingIface(i)}>Delete</button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>

        <section>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold text-gray-900">VLANs 802.1q</h2>
            <button className="btn-primary" onClick={() => setCreatingVlan(true)}>Add a VLAN</button>
          </div>
          <p className="text-xs text-gray-700 mb-2">
            A VLAN interface creates a logical separation on an existing physical
            interface (typically a trunk port). MurOS runs
            <code className="font-mono"> ip link add ... type vlan</code> in the kernel.
          </p>
          <div className="border border-gray-200 rounded-md overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-700 text-xs uppercase tracking-wider">
                <tr>
                  <th className="text-center px-3 py-2 w-16">Enabled</th>
                  <th className="text-left px-3 py-2 w-12">ID</th>
                  <th className="text-left px-3 py-2 w-40">Name</th>
                  <th className="text-left px-3 py-2 w-32">Zone</th>
                  <th className="text-left px-3 py-2 w-24">Parent</th>
                  <th className="text-left px-3 py-2 w-20">Tag</th>
                  <th className="text-left px-3 py-2 w-24">IP mode</th>
                  <th className="text-left px-3 py-2 w-44">Address</th>
                  <th className="text-left px-3 py-2 w-40">Gateway</th>
                  <th className="text-right px-3 py-2 w-28"></th>
                </tr>
              </thead>
              <tbody>
                {loading && <TableSkeleton rows={5} cols={10} />}
                {!loading && vlanInterfaces.length === 0 && (
                  <tr><td colSpan={10}>
                    <EmptyState
                      icon={<NetworkIcon size={20} />}
                      text={search ? 'No VLAN matches the filter' : 'No VLAN yet'}
                      hint={search ? undefined : '802.1q VLANs let you segment one physical interface into several logical zones (LAN/DMZ/Guest).'}
                      action={!search && (
                        <button className="btn-primary" onClick={() => setCreatingVlan(true)}>Add a VLAN</button>
                      )}
                    />
                  </td></tr>
                )}
                {vlanInterfaces.map((v) => {
                  const pendingDel = !!v.pending_delete
                  const dim = pendingDel ? 'opacity-50 line-through' : (!v.enabled ? 'opacity-50' : '')
                  const rowBg = pendingDel ? 'bg-red-50' : (!v.enabled ? 'bg-gray-50' : '')
                  const zone = v.zone_id ? zoneById.get(v.zone_id) : null
                  return (
                    <tr key={v.id} className={`border-t border-gray-200 hover:bg-gray-50 ${rowBg}`}>
                      <td className="px-3 py-2 text-center">
                        <Toggle size="sm" checked={v.enabled} onChange={() => toggleIfaceEnabled(v)} disabled={pendingDel} />
                      </td>
                      <td className={`px-3 py-2 font-mono text-gray-700 ${dim}`}>{v.id}</td>
                      <td className={`px-3 py-2 font-mono ${dim}`}>
                        {v.name}
                        {pendingDel && (
                          <span className="ml-2 inline-block px-1.5 py-0.5 text-[10px] font-semibold rounded bg-red-100 text-red-800 border border-red-200 no-underline">
                            pending delete
                          </span>
                        )}
                      </td>
                      <td className={`px-3 py-2 ${dim}`}>
                        {zone ? <ZoneBadge name={zone.name} /> : <span className="text-xs text-gray-400">unzoned</span>}
                      </td>
                      <td className={`px-3 py-2 font-mono text-xs text-gray-800 ${dim}`}>{v.parent_interface || '-'}</td>
                      <td className={`px-3 py-2 font-mono text-xs text-gray-800 ${dim}`}>{v.vlan_id ?? '-'}</td>
                      <td className={`px-3 py-2 font-mono text-xs text-gray-800 ${dim}`}>{v.ip_mode}</td>
                      <td className={`px-3 py-2 font-mono text-xs text-gray-800 ${dim}`}>
                        {v.ip_mode === 'dhcp' ? <span className="text-gray-600">dynamic</span>
                          : v.ip_address || <span className="text-gray-600">not configured</span>}
                      </td>
                      <td className={`px-3 py-2 font-mono text-xs ${dim}`}>
                        {v.gateway
                          ? <span className="text-gray-800">{v.gateway}</span>
                          : <span className="text-gray-500">-</span>}
                      </td>
                      <td className="px-3 py-2 text-right">
                        {pendingDel ? (
                          <button
                            className="btn-ghost py-1"
                            onClick={async () => { await api.interfaces.cancelDelete(v.id); reload() }}
                            title="Cancel the pending deletion before Apply"
                          >Cancel delete</button>
                        ) : (
                          <>
                            <button className="btn-ghost py-1" onClick={() => setEditingVlan(v)}>Edit</button>
                            <button className="btn-ghost py-1 text-red-700 hover:text-red-800" onClick={() => setDeletingVlan(v)}>Delete</button>
                          </>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {/* Modals : interfaces */}
      <Modal open={creatingIface || !!importingName} onClose={() => { setCreatingIface(false); setImportingName(null) }} title="New interface">
        <InterfaceForm
          defaultName={importingName || undefined}
          onCancel={() => { setCreatingIface(false); setImportingName(null) }}
          onSubmit={async (data) => {
            await api.interfaces.create(data)
            setCreatingIface(false); setImportingName(null); reload()
          }}
        />
      </Modal>
      <Modal open={!!editingIface} onClose={() => setEditingIface(null)} title={`Edit interface ${editingIface?.name || ''}`}>
        {editingIface && (
          <InterfaceForm
            iface={editingIface}
            onCancel={() => setEditingIface(null)}
            onSubmit={async (data) => { await api.interfaces.update(editingIface.id, data); setEditingIface(null); reload() }}
          />
        )}
      </Modal>
      <Modal
        open={!!deletingIface}
        onClose={() => setDeletingIface(null)}
        title="Delete the interface"
        size="sm"
        footer={
          <>
            <button className="btn-secondary" onClick={() => setDeletingIface(null)}>Cancel</button>
            <button
              className="btn-danger"
              onClick={async () => {
                if (deletingIface) await api.interfaces.remove(deletingIface.id)
                setDeletingIface(null); reload()
              }}
            >Delete</button>
          </>
        }
      >
        {deletingIface && (
          <p className="text-sm text-gray-800">
            L'interface <span className="font-mono text-gray-900">{deletingIface.name}</span> will be deleted.
            Rules referencing it will not be affected, but NAT rules pointing to it will lose their target.
          </p>
        )}
      </Modal>

      {/* Modals : VLANs */}
      <Modal open={creatingVlan} onClose={() => setCreatingVlan(false)} title="New VLAN" size="lg">
        <VlanForm
          interfaces={interfaces}
          onCancel={() => setCreatingVlan(false)}
          onSubmit={async (data) => { await api.interfaces.create(data); setCreatingVlan(false); reload() }}
        />
      </Modal>
      <Modal open={!!editingVlan} onClose={() => setEditingVlan(null)} title={`Edit VLAN ${editingVlan?.name || ''}`} size="lg">
        {editingVlan && (
          <VlanForm
            vlan={editingVlan}
            interfaces={interfaces}
            onCancel={() => setEditingVlan(null)}
            onSubmit={async (data) => { await api.interfaces.update(editingVlan.id, data); setEditingVlan(null); reload() }}
          />
        )}
      </Modal>
      <Modal
        open={!!deletingVlan}
        onClose={() => setDeletingVlan(null)}
        title="Delete the VLAN"
        size="sm"
        footer={
          <>
            <button className="btn-secondary" onClick={() => setDeletingVlan(null)}>Cancel</button>
            <button
              className="btn-danger"
              onClick={async () => {
                if (deletingVlan) await api.interfaces.remove(deletingVlan.id)
                setDeletingVlan(null); reload()
              }}
            >Delete</button>
          </>
        }
      >
        {deletingVlan && (
          <p className="text-sm text-gray-800">
            VLAN <span className="font-mono text-gray-900">{deletingVlan.name}</span> will be marked
            for deletion. The kernel interface stays alive until you click <em>Apply</em>, at which point
            it is removed via <code className="font-mono">ip link delete</code> and the DB row is dropped.
            You can cancel the pending delete from the table before applying.
          </p>
        )}
      </Modal>

      {/* Modal import */}
      <Modal open={importOpen} onClose={() => setImportOpen(false)} title="Interfaces detected on the system" size="lg">
        {!systemIfs && <p className="text-sm text-gray-700">Scanning...</p>}
        {systemIfs && systemIfs.length === 0 && <p className="text-sm text-gray-700">No interface detected.</p>}
        {systemIfs && systemIfs.length > 0 && (
          <div className="border border-gray-200 rounded">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-700 text-xs uppercase tracking-wider">
                <tr>
                  <th className="text-left px-3 py-2 w-40">Name</th>
                  <th className="text-left px-3 py-2 w-20">State</th>
                  <th className="text-left px-3 py-2 w-20">MTU</th>
                  <th className="text-left px-3 py-2">Addresss</th>
                  <th className="text-right px-3 py-2 w-28"></th>
                </tr>
              </thead>
              <tbody>
                {systemIfs.map((si) => {
                  const known = knownNames.has(si.name)
                  return (
                    <tr key={si.name} className="border-t border-gray-200">
                      <td className="px-3 py-2 font-mono">
                        {si.name}
                        {si.is_virtual && <span className="ml-2 badge bg-gray-100 text-gray-700 border border-gray-200">virtuel</span>}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-800">{si.state}</td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-800">{si.mtu}</td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-800">
                        {si.addresses.length ? si.addresses.join(', ') : <span className="text-gray-500">-</span>}
                      </td>
                      <td className="px-3 py-2 text-right">
                        {known ? (
                          <span className="text-xs text-gray-600">already added</span>
                        ) : (
                          <button
                            className="btn-secondary py-1"
                            onClick={() => { setImportOpen(false); setImportingName(si.name) }}
                          >Add</button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Modal>
    </div>
  )
}
