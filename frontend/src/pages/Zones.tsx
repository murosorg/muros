import { useEffect, useMemo, useState } from 'react'
import { api, FirewallRule, Interface, Zone } from '../lib/api'
import Modal from '../components/Modal'
import PageHeader from '../components/PageHeader'
import ApplyFirewallButton from '../components/ApplyFirewallButton'
import EmptyState from '../components/EmptyState'
import RulesetModal from '../components/RulesetModal'
import ZoneForm from '../components/ZoneForm'
import TableSkeleton from '../components/TableSkeleton'
import { ErrorBlock } from '../components/Alerts'
import { ZoneBadge } from '../lib/zoneColor'
import { Boxes } from 'lucide-react'
import { useConfirm } from '../components/ConfirmModal'

export default function Zones() {
  const [zones, setZones] = useState<Zone[]>([])
  const [interfaces, setInterfaces] = useState<Interface[]>([])
  const [rules, setRules] = useState<FirewallRule[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [creatingZone, setCreatingZone] = useState(false)
  const [editingZone, setEditingZone] = useState<Zone | null>(null)
  const [showRuleset, setShowRuleset] = useState(false)
  const { confirm, ConfirmHost } = useConfirm()

  const askDeleteZone = async (z: Zone) => {
    const ok = await confirm({
      title: `Delete zone "${z.name}"`,
      message: (
        <p>The zone <span className="font-mono text-gray-900">{z.name}</span> will be deleted.
          Attached interfaces will be detached but not deleted.</p>
      ),
      destructive: true,
      requireText: z.name,
    })
    if (!ok) return
    try { await api.zones.remove(z.id); reload() }
    catch (e) { setError((e as Error).message) }
  }

  const reload = async () => {
    setLoading(true)
    try {
      const [z, i, r] = await Promise.all([
        api.zones.list(), api.interfaces.list(), api.rules.list(),
      ])
      setZones(z); setInterfaces(i); setRules(r); setError(null)
    } catch (e) {
      setError(String(e))
    } finally { setLoading(false) }
  }

  useEffect(() => { reload() }, [])

  return (
    <div>
      <PageHeader
        icon={<Boxes size={16} />}
       
        title="Zones"
        description="Group interfaces. Filter rules apply between zones."
        actions={<ApplyFirewallButton onClick={() => setShowRuleset(true)} onView={() => setShowRuleset(true)} />}
      />

      {showRuleset && <RulesetModal onClose={() => setShowRuleset(false)} />}

      <div className="px-6 py-4 space-y-6">
        {error && (
          <ErrorBlock message={error} />
        )}

        <section>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold text-gray-900">Declared zones</h2>
            <button className="btn-primary" onClick={() => setCreatingZone(true)}>Add a zone</button>
          </div>
        <div className="border border-gray-200 rounded-md overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-700 text-xs uppercase tracking-wider">
              <tr>
                <th className="text-left px-3 py-2 w-12">ID</th>
                <th className="text-left px-3 py-2 w-40">Name</th>
                <th className="text-left px-3 py-2">Description</th>
                <th className="text-left px-3 py-2 w-64">Attached interfaces</th>
                <th className="text-right px-3 py-2 w-28"></th>
              </tr>
            </thead>
            <tbody>
              {loading && <TableSkeleton rows={5} cols={5} />}
              {!loading && zones.length === 0 && (
                <tr><td colSpan={5}>
                  <EmptyState
                    icon={<Boxes size={20} />}
                    text="No zone declared"
                    hint="Zones group interfaces logically (WAN, LAN, DMZ). Firewall rules and NAT rely on them."
                    action={<button className="btn-primary" onClick={() => setCreatingZone(true)}>Add a zone</button>}
                  />
                </td></tr>
              )}
              {zones.map((z) => {
                const ifs = interfaces.filter((i) => i.zone_id === z.id)
                return (
                  <tr key={z.id} className="border-t border-gray-200 hover:bg-gray-50">
                    <td className="px-3 py-2 font-mono text-gray-700">{z.id}</td>
                    <td className="px-3 py-2"><ZoneBadge name={z.name} /></td>
                    <td className="px-3 py-2 text-gray-800">{z.description || ''}</td>
                    <td className="px-3 py-2 font-mono text-xs text-gray-800">
                      {ifs.length ? ifs.map((i) => i.name).join(', ') : <span className="text-gray-500">-</span>}
                    </td>
                    <td className="px-3 py-2 text-right whitespace-nowrap">
                      <div className="inline-flex items-center gap-1 justify-end">
                        <button className="btn-ghost py-1" onClick={() => setEditingZone(z)}>Edit</button>
                        <button className="btn-ghost py-1 text-red-700 hover:text-red-800" onClick={() => askDeleteZone(z)}>Delete</button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        </section>

        {zones.length >= 2 && (
          <section>
            <h2 className="text-sm font-semibold text-gray-900 mb-1">Zone policy matrix</h2>
            <p className="text-xs text-gray-700 mb-2">
              Aggregated from the firewall rules currently in the DB (forward chain only).
              Each cell is a summary: a green <em>accept</em> means at least one enabled accept rule exists
              for that direction. <em>drop</em>/<em>reject</em> means at least one block rule. <em>mixed</em>
              means both directions exist (depends on rule order, click to see rules).
              Empty cell = no rule, so the default (drop) applies.
            </p>
            <PolicyMatrix zones={zones} rules={rules} />
          </section>
        )}
      </div>

      {/* Modals : zones */}
      <Modal open={creatingZone} onClose={() => setCreatingZone(false)} title="New zone">
        <ZoneForm
          interfaces={interfaces}
          onCancel={() => setCreatingZone(false)}
          onSubmit={async (data) => { await api.zones.create(data); setCreatingZone(false); reload() }}
        />
      </Modal>
      <Modal open={!!editingZone} onClose={() => setEditingZone(null)} title={`Edit zone ${editingZone?.name || ''}`}>
        {editingZone && (
          <ZoneForm
            zone={editingZone}
            interfaces={interfaces}
            onCancel={() => setEditingZone(null)}
            onChange={reload}
            onSubmit={async (data) => { await api.zones.update(editingZone.id, data); setEditingZone(null); reload() }}
          />
        )}
      </Modal>
      <ConfirmHost />
    </div>
  )
}

function PolicyMatrix({ zones, rules }: { zones: Zone[]; rules: FirewallRule[] }) {
  // Pour chaque (src, dst) on calcule l'action dominante :
  //   - 'accept'  : au moins une rule accept enabled, et pas de drop/reject
  //   - 'block'   : au moins une drop/reject enabled, et pas d'accept
  //   - 'mixed'   : les deux
  //   - undefined : aucune rule
  const cellMap = useMemo(() => {
    const m = new Map<string, { accept: number; block: number; rules: FirewallRule[] }>()
    for (const r of rules) {
      if (!r.enabled) continue
      if (r.chain !== 'forward') continue
      const key = `${r.src_zone_id ?? 'any'}-${r.dst_zone_id ?? 'any'}`
      const cell = m.get(key) ?? { accept: 0, block: 0, rules: [] }
      if (r.action === 'accept') cell.accept++
      else if (r.action === 'drop' || r.action === 'reject') cell.block++
      cell.rules.push(r)
      m.set(key, cell)
    }
    return m
  }, [rules])

  const cellFor = (srcId: number, dstId: number) => {
    const c = cellMap.get(`${srcId}-${dstId}`)
    if (!c) return null
    const verdict = c.accept > 0 && c.block === 0 ? 'accept'
      : c.block > 0 && c.accept === 0 ? 'block'
      : 'mixed'
    return { verdict, count: c.rules.length }
  }

  return (
    <div className="border border-gray-200 rounded-md overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-gray-700 text-xs uppercase tracking-wider">
          <tr>
            <th className="text-left px-3 py-2 w-40">From \\ To</th>
            {zones.map((z) => (
              <th key={z.id} className="text-left px-3 py-2">
                <ZoneBadge name={z.name} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {zones.map((src) => (
            <tr key={src.id} className="border-t border-gray-200">
              <td className="px-3 py-2"><ZoneBadge name={src.name} /></td>
              {zones.map((dst) => {
                const cell = cellFor(src.id, dst.id)
                const isSelf = src.id === dst.id
                const bg = !cell
                  ? (isSelf ? 'bg-gray-50' : 'bg-white')
                  : cell.verdict === 'accept' ? 'bg-emerald-50'
                  : cell.verdict === 'block'  ? 'bg-red-50'
                  : 'bg-amber-50'
                return (
                  <td key={dst.id} className={`px-3 py-2 text-xs ${bg}`}>
                    {!cell ? (
                      <span className="text-gray-400">default (drop)</span>
                    ) : (
                      <span className="font-mono">
                        {cell.verdict === 'accept' && <span className="text-emerald-800">accept</span>}
                        {cell.verdict === 'block'  && <span className="text-red-800">block</span>}
                        {cell.verdict === 'mixed'  && <span className="text-amber-800">mixed</span>}
                        <span className="text-gray-500 ml-1">({cell.count})</span>
                      </span>
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
