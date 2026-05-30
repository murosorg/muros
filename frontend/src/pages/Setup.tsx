import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type SetupInterface } from '../lib/api'
import { ErrorBlock } from '../components/Alerts'

// First-boot onboarding. The single mandatory step: tell MurOS which NIC
// faces the trusted LAN. Picking the Internet (WAN) interface is optional:
// a single-NIC box (WAN reached over a VLAN later) is a valid posture, just
// like OPNsense. Applying it assigns the zones, drops the permissive
// bootstrap rules and pushes the network + firewall configuration. After
// that the box reaches its final posture: LAN can reach everything, the WAN
// (when assigned) is default-deny.
export default function Setup() {
  const nav = useNavigate()
  const [interfaces, setInterfaces] = useState<SetupInterface[]>([])
  const [lan, setLan] = useState('')
  const [wan, setWan] = useState('')
  const [lanCidr, setLanCidr] = useState('192.168.1.1/24')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [step, setStep] = useState<string>('')

  useEffect(() => {
    api.setup.state().then((s) => {
      if (s.completed) { nav('/', { replace: true }); return }
      setInterfaces(s.interfaces)
      const names = s.interfaces.map((i) => i.name)
      // The first NIC is the trusted LAN by default. A second NIC, when
      // present, is pre-selected as the WAN; with a single NIC the WAN
      // stays unset.
      if (names[0]) setLan(names[0])
      if (names[1]) setWan(names[1])
    }).catch((e) => setError(String(e)))
  }, [nav])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      setStep('Assigning zones...')
      await api.setup.apply({ lan_interface: lan, lan_cidr: lanCidr, wan_interface: wan || null })
      // Push the staged network + firewall configuration using the normal
      // apply paths (best-effort: a fresh box may not have a live link yet).
      setStep('Applying network...')
      try { await api.network.apply() } catch { /* keep going */ }
      setStep('Applying firewall...')
      try { await api.apply.run(60) } catch { /* keep going */ }
      nav('/', { replace: true })
    } catch (e) {
      setError(String(e)); setBusy(false); setStep('')
    }
  }

  return (
    <div className="min-h-full flex items-center justify-center bg-gray-50 py-10">
      <form onSubmit={submit} className="w-full max-w-lg bg-white border border-gray-200 rounded-md p-6 shadow-sm">
        <div className="bg-neutral-900 rounded-md px-4 py-3 mb-6 flex items-center justify-center">
          <img src="/logo.svg" alt="MurOS" className="h-10 w-auto" />
        </div>
        <h1 className="text-lg font-semibold text-gray-900 mb-1">Initial setup</h1>
        <p className="text-sm text-gray-600 mb-5">
          Tell MurOS which interface faces your trusted LAN. The LAN can
          reach the firewall and its services. Selecting the WAN (Internet)
          interface is optional and can be done later from the Network page.
        </p>

        {error && <div className="mb-4"><ErrorBlock message={error} /></div>}

        <div className="mb-4">
          <label className="label">LAN interface (trusted)</label>
          <select className="input" value={lan} onChange={(e) => setLan(e.target.value)}>
            {interfaces.map((i) => <option key={i.name} value={i.name}>{i.name}</option>)}
          </select>
        </div>

        <div className="mb-5">
          <label className="label">LAN address (CIDR)</label>
          <input className="input font-mono" value={lanCidr}
            onChange={(e) => setLanCidr(e.target.value)} placeholder="192.168.1.1/24" />
          <p className="text-xs text-gray-500 mt-1">
            The firewall's own address on the LAN, e.g. 192.168.1.1/24.
          </p>
        </div>

        <div className="mb-4">
          <label className="label">WAN interface (Internet, DHCP client) - optional</label>
          <select className="input" value={wan} onChange={(e) => setWan(e.target.value)}>
            <option value="">None (assign later)</option>
            {interfaces.map((i) => <option key={i.name} value={i.name}>{i.name}</option>)}
          </select>
          <p className="text-xs text-gray-500 mt-1">
            Leave unset on a single-NIC box; you can reach the Internet over
            a VLAN and assign it from the Network page.
          </p>
        </div>

        {wan && lan && wan === lan && (
          <p className="text-xs text-amber-700 mb-3">WAN and LAN must be different interfaces.</p>
        )}

        <button type="submit" className="btn-primary w-full justify-center"
          disabled={busy || !lan || (!!wan && wan === lan) || !lanCidr}>
          {busy ? (step || 'Applying...') : 'Finish setup'}
        </button>
      </form>
    </div>
  )
}
