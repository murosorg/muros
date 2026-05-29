import { useEffect, useMemo, useState } from 'react'
import { api, Interface, StaticRoute } from '../lib/api'
import Modal from '../components/Modal'
import PageHeader from '../components/PageHeader'
import EmptyState from '../components/EmptyState'
import NetworkEnvironmentWarning from '../components/NetworkEnvironmentWarning'
import RouteForm from '../components/RouteForm'
import Toggle from '../components/Toggle'
import ApplyNetworkButton from '../components/ApplyNetworkButton'
import KebabMenu from '../components/KebabMenu'
import DismissibleNote from '../components/DismissibleNote'
import TableSkeleton from '../components/TableSkeleton'
import { ErrorBlock } from '../components/Alerts'
import { useConfirm } from '../components/ConfirmModal'
import { toast } from '../components/Toast'
import { Route } from 'lucide-react'

export default function RoutesPage() {
  const [routes, setRoutes] = useState<StaticRoute[]>([])
  const [interfaces, setInterfaces] = useState<Interface[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<StaticRoute | null>(null)
  const [toDelete, setToDelete] = useState<StaticRoute | null>(null)
  const { confirm, ConfirmHost } = useConfirm()

  const adoptKernel = async () => {
    const ok = await confirm({
      title: 'Import kernel config',
      message: 'Imports interfaces, IPs and routes currently live in the kernel, and pins them in the MurOS DB. Useful after installing on an already-configured machine or for recovery. Nothing is pushed to the kernel, we only document the existing state.',
      confirmLabel: 'Import',
    })
    if (!ok) return
    try {
      const r = await api.network.adopt()
      if (r.skipped) {
        toast.info('Adoption already done, nothing to do.')
      } else {
        toast.success(`Adopted: ${r.interfaces_touched} interface(s), ${r.routes_touched} route(s).`)
      }
      reload()
    } catch (e) {
      setError(String(e))
    }
  }

  const ifById = useMemo(() => {
    const m = new Map<number, Interface>()
    interfaces.forEach((i) => m.set(i.id, i))
    return m
  }, [interfaces])

  // Default gateways derived from interface configuration. Each interface
  // carries an optional gateway field (populated by static config or the
  // kernel adoption flow). MurOS materialises it as the default route at
  // apply time, so we surface a synthesized read-only row here. This
  // keeps the Routing page honest: an admin with a working default
  // gateway must see it, even when no explicit StaticRoute row exists.
  const derivedDefaults = useMemo(
    () => interfaces.filter((i) => i.gateway && i.gateway.trim() !== ''),
    [interfaces],
  )

  const reload = async () => {
    setLoading(true)
    try {
      const [r, i] = await Promise.all([
        api.routes.list(),
        api.interfaces.list(),
      ])
      setRoutes(r)
      setInterfaces(i)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { reload() }, [])

  const toggleEnabled = async (r: StaticRoute) => {
    await api.routes.update(r.id, { enabled: !r.enabled })
    reload()
  }

  return (
    <div>
      <ConfirmHost />
      <PageHeader
        icon={<Route size={16} />}
       
        title="Routing"
        description="Default gateway and static routes."
        actions={<ApplyNetworkButton />}
      />

      <div className="px-6 py-4 space-y-4">
        <NetworkEnvironmentWarning />
        {error && (
          <ErrorBlock message={error} />
        )}

        <DismissibleNote id="routing-intro" variant="tip">
          Static routes apply on top of connected routes (auto-derived from interface IPs).
          You typically only need to add a route here for a non-default gateway or a destination
          beyond the local LAN. Default routes go here too. The kernel picks the longest-prefix
          match first, then the lowest metric to break ties.
        </DismissibleNote>

        <section>
          <div className="flex items-center justify-between mb-1">
            <h2 className="text-sm font-semibold text-gray-900">Routes</h2>
            <div className="flex items-center gap-2">
              <button className="btn-primary" onClick={() => setCreating(true)}>Add a route</button>
              <KebabMenu items={[
                { label: 'Re-import from kernel', onClick: adoptKernel, hint: 'Re-import the current kernel config into the MurOS DB (recovery)' },
              ]} />
            </div>
          </div>
          <p className="text-xs text-gray-700 mb-2">
            MurOS pushes these routes to the kernel via <code className="font-mono mx-1">ip route</code> and
            replays them at every reboot. Routes directly connected to interfaces (scope=link)
            are not displayed here, they derive automatically from the IPs declared on the
            interfaces. For kernel debug, see the Diagnostic page.
          </p>
          <div className="border border-gray-200 rounded-md overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-700 text-xs uppercase tracking-wider">
                <tr>
                  <th className="text-left px-3 py-2 w-12">ID</th>
                  <th className="text-left px-3 py-2 w-20">State</th>
                  <th className="text-left px-3 py-2">Destination</th>
                  <th className="text-left px-3 py-2">Gateway</th>
                  <th className="text-left px-3 py-2 w-28">Interface</th>
                  <th className="text-left px-3 py-2 w-20">Metric</th>
                  <th className="text-left px-3 py-2">Comment</th>
                  <th className="text-right px-3 py-2 w-28"></th>
                </tr>
              </thead>
              <tbody>
                {loading && <TableSkeleton rows={5} cols={8} />}
                {!loading && routes.length === 0 && derivedDefaults.length === 0 && (
                  <tr><td colSpan={8}><EmptyState
                    icon={<Route size={20} />}
                    text="No static route"
                    hint="Routing relies on connected routes + the DHCP default gateway. Add a route to declare a specific next hop (remote LAN, VPN, etc.)."
                    action={<button className="btn-primary" onClick={() => setCreating(true)}>Add a route</button>}
                  /></td></tr>
                )}
                {derivedDefaults.map((iface) => (
                  <tr key={`default-${iface.id}`} className="border-t border-gray-200 bg-gray-50/60">
                    <td className="px-3 py-2 font-mono text-gray-500" title="No DB row. This route is auto-generated from the interface gateway.">auto</td>
                    <td className="px-3 py-2">
                      <span
                        className="inline-flex items-center gap-1 text-[11px] text-emerald-800 bg-emerald-50 border border-emerald-200 rounded px-1.5 py-0.5"
                        title="The route is active in the kernel. It is generated from the gateway set on the interface, not from a static route entry."
                      >
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" aria-hidden="true" />
                        active
                      </span>
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-gray-900">default</td>
                    <td className="px-3 py-2 font-mono text-xs text-gray-800">{iface.gateway}</td>
                    <td className="px-3 py-2 font-mono text-xs text-gray-800">{iface.name}</td>
                    <td className="px-3 py-2 font-mono text-xs text-gray-800">0</td>
                    <td className="px-3 py-2 text-gray-700 text-xs italic">
                      Set on interface <span className="font-mono not-italic">{iface.name}</span> (Network page)
                    </td>
                    <td className="px-3 py-2 text-right text-xs text-gray-500" title="To edit this route, change the gateway on the matching interface in the Network page.">
                      managed on Network
                    </td>
                  </tr>
                ))}
                {routes.map((r) => {
                  const iface = r.interface_id ? ifById.get(r.interface_id) : undefined
                  return (
                    <tr key={r.id} className={`border-t border-gray-200 hover:bg-gray-50 ${!r.enabled ? 'opacity-50' : ''}`}>
                      <td className="px-3 py-2 font-mono text-gray-700">{r.id}</td>
                      <td className="px-3 py-2">
                        <Toggle size="sm" checked={r.enabled} onChange={() => toggleEnabled(r)} />
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-900">{r.destination}</td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-800">{r.gateway || <span className="text-gray-500">-</span>}</td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-800">{iface?.name || <span className="text-gray-600">auto</span>}</td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-800">{r.metric}</td>
                      <td className="px-3 py-2 text-gray-800">{r.comment || ''}</td>
                      <td className="px-3 py-2 text-right">
                        <button className="btn-ghost py-1" onClick={() => setEditing(r)}>Edit</button>
                        <button className="btn-ghost py-1 text-red-700 hover:text-red-800" onClick={() => setToDelete(r)}>Delete</button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>

      </div>

      <Modal open={creating} onClose={() => setCreating(false)} title="New route" size="md">
        <RouteForm
          interfaces={interfaces}
          onCancel={() => setCreating(false)}
          onSubmit={async (data) => { await api.routes.create(data); setCreating(false); reload() }}
        />
      </Modal>

      <Modal open={!!editing} onClose={() => setEditing(null)} title={`Edit route #${editing?.id}`} size="md">
        {editing && (
          <RouteForm
            route={editing}
            interfaces={interfaces}
            onCancel={() => setEditing(null)}
            onSubmit={async (data) => { await api.routes.update(editing.id, data); setEditing(null); reload() }}
          />
        )}
      </Modal>

      <Modal
        open={!!toDelete}
        onClose={() => setToDelete(null)}
        title="Delete the route"
        size="sm"
        footer={
          <>
            <button className="btn-secondary" onClick={() => setToDelete(null)}>Cancel</button>
            <button
              className="btn-danger"
              onClick={async () => {
                if (toDelete) await api.routes.remove(toDelete.id)
                setToDelete(null)
                reload()
              }}
            >
              Delete
            </button>
          </>
        }
      >
        {toDelete && (
          <p className="text-sm text-gray-800">
            The route <span className="font-mono text-gray-900">{toDelete.destination}</span> will be deleted
            from the MurOS DB and removed from the kernel immediately.
          </p>
        )}
      </Modal>
    </div>
  )
}
