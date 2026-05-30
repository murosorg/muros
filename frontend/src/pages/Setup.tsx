import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type SetupInterface } from '../lib/api'
import { ErrorBlock } from '../components/Alerts'

const CIDR_PLACEHOLDER = '192.168.1.1/24'

// First-boot onboarding. The single mandatory step: tell MurOS which NIC
// faces the trusted LAN, and its address. That is all the security posture
// needs: the LAN zone is trusted and can reach the firewall and its
// services, while every other interface is firewall-filtered (default-deny)
// whether or not it carries a name. The Internet uplink (WAN) is a
// connectivity concern, not a security one, and is configured afterwards
// from the Network page where the addressing mode (DHCP, static, PPPoE),
// MTU and the rest live. Applying assigns the LAN zone, drops the permissive
// bootstrap rules and pushes the network + firewall configuration.
//
// Lock-out safety: the box reaches this page over the address it picked up
// at install time (typically DHCP), which the seed froze on the interface.
// We pre-fill the CIDR with that live address so finishing the wizard does
// NOT move the management IP under the operator's feet. If they deliberately
// type a different address we surface a warning, because applying it will
// drop the current session and they will have to reconnect on the new IP.
export default function Setup() {
  const nav = useNavigate()
  const [interfaces, setInterfaces] = useState<SetupInterface[]>([])
  const [lan, setLan] = useState('')
  const [lanCidr, setLanCidr] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [step, setStep] = useState<string>('')

  // Current (live) address of the selected LAN interface, as frozen by the
  // seed from the kernel. Empty when the NIC has no IPv4 yet.
  const currentIp = interfaces.find((i) => i.name === lan)?.ip_address ?? ''

  // Selecting a LAN interface pre-fills the CIDR with its live address so
  // the management IP stays put. Falls back to a placeholder example when
  // the NIC has no address.
  const selectLan = (name: string) => {
    setLan(name)
    const ip = interfaces.find((i) => i.name === name)?.ip_address
    setLanCidr(ip || CIDR_PLACEHOLDER)
  }

  useEffect(() => {
    api.setup.state().then((s) => {
      if (s.completed) { nav('/', { replace: true }); return }
      setInterfaces(s.interfaces)
      // The first NIC is the trusted LAN by default; seed its live address.
      const first = s.interfaces[0]
      if (first) {
        setLan(first.name)
        setLanCidr(first.ip_address || CIDR_PLACEHOLDER)
      }
    }).catch((e) => setError(String(e)))
  }, [nav])

  // True when the operator typed an address other than the one the box is
  // currently reachable on: applying it will change the management IP.
  const ipWillChange = !!currentIp && !!lanCidr && lanCidr.trim() !== currentIp

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
          <select className="input" value={lan} onChange={(e) => selectLan(e.target.value)}>
            {interfaces.map((i) => (
              <option key={i.name} value={i.name}>
                {i.name}{i.ip_address ? ` (${i.ip_address})` : ''}
              </option>
            ))}
          </select>
        </div>

        <div className="mb-5">
          <label className="label">LAN address (CIDR)</label>
          <input className="input font-mono" value={lanCidr}
            onChange={(e) => setLanCidr(e.target.value)} placeholder={CIDR_PLACEHOLDER} />
          {currentIp ? (
            <p className="text-xs text-gray-500 mt-1">
              Pre-filled with the address this box is currently reachable on.
              Keep it to stay connected after finishing.
            </p>
          ) : (
            <p className="text-xs text-gray-500 mt-1">
              The firewall's own address on the LAN, e.g. {CIDR_PLACEHOLDER}.
            </p>
          )}
          {ipWillChange && (
            <p className="text-xs text-amber-700 mt-2">
              This differs from the current address ({currentIp}). Finishing
              setup will move the firewall to {lanCidr.trim()} and drop this
              session; you will need to reconnect on the new address.
            </p>
          )}
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
