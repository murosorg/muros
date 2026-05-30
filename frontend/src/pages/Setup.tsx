import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type SetupInterface } from '../lib/api'
import { ErrorBlock } from '../components/Alerts'

// First-boot onboarding. The single mandatory step: tell MurOS which NIC
// faces the trusted LAN, and its address. That is all the security posture
// needs: the LAN zone is trusted and can reach the firewall and its
// services, while every other interface is firewall-filtered (default-deny)
// whether or not it carries a name. The Internet uplink (WAN) is a
// connectivity concern, not a security one, and is configured afterwards
// from the Network page where the addressing mode (DHCP, static, PPPoE),
// MTU and the rest live. Applying assigns the LAN zone, drops the permissive
// bootstrap rules and pushes the network + firewall configuration.
export default function Setup() {
  const nav = useNavigate()
  const [interfaces, setInterfaces] = useState<SetupInterface[]>([])
  const [lan, setLan] = useState('')
  const [lanCidr, setLanCidr] = useState('192.168.1.1/24')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [step, setStep] = useState<string>('')

  useEffect(() => {
    api.setup.state().then((s) => {
      if (s.completed) { nav('/', { replace: true }); return }
      setInterfaces(s.interfaces)
      const names = s.interfaces.map((i) => i.name)
      // The first NIC is the trusted LAN by default.
      if (names[0]) setLan(names[0])
    }).catch((e) => setError(String(e)))
  }, [nav])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      setStep('Assigning zones...')
      await api.setup.apply({ lan_interface: lan, lan_cidr: lanCidr })
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
          reach the firewall and its services; every other interface is
          filtered by default. Configure your Internet uplink afterwards
          from the Network page.
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

        <p className="text-xs text-gray-500 mb-5">
          Every interface other than the LAN is left in no zone and stays
          default-deny at the firewall. Wire your Internet uplink from the
          Network page once setup is done.
        </p>

        <button type="submit" className="btn-primary w-full justify-center"
          disabled={busy || !lan || !lanCidr}>
          {busy ? (step || 'Applying...') : 'Finish setup'}
        </button>
      </form>
    </div>
  )
}
