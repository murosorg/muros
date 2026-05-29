import { useEffect, useState } from 'react'
import { api } from '../lib/api'

/**
 * Bandeau de diagnostic affiche en haut des pages Network et Routage.
 * Avertit quand un autre gestionnaire reseau tourne en parallele
 * (NetworkManager, systemd-networkd...) car il va ecraser les valeurs
 * poussees par MurOS.
 *
 * Sur l'appliance Debian 13 cible, ces services sont desactives par
 * muros-boot, le bandeau est masque. Sur Ubuntu / Fedora de dev, il
 * apparait avec la liste des concurrents detectes.
 */
export default function NetworkEnvironmentWarning() {
  const [env, setEnv] = useState<{ apply_enabled: boolean; competing_managers: string[] } | null>(null)

  useEffect(() => {
    api.network.environment().then(setEnv).catch(() => setEnv(null))
  }, [])

  if (!env) return null
  const { apply_enabled, competing_managers } = env
  if (apply_enabled && competing_managers.length === 0) return null

  return (
    <div className="text-sm border border-amber-300 bg-amber-50 rounded p-3 text-amber-900 space-y-1">
      {!apply_enabled && (
        <div>
          <span className="font-semibold">Dry-run mode active.</span>{' '}
          IP, MTU, VLAN and route changes are saved in the database but
          not pushed to the kernel. To apply for real, set
          <code className="font-mono">MUROS_APPLY=true</code> on the target.
        </div>
      )}
      {competing_managers.length > 0 && (
        <div>
          <span className="font-semibold">Competing network manager detected:</span>{' '}
          <code className="font-mono">{competing_managers.join(', ')}</code>.
          {' '}
          This service will likely re-apply the system config (DHCP,
          netplan...) a few seconds after each change pushed by MurOS.
          MurOS is designed to drive the network alone; on the target
          Debian 13 appliance, those services are disabled. On this
          machine, disable the competitor
          (<code className="font-mono">systemctl disable --now &lt;service&gt;</code>)
          to avoid conflicts.
        </div>
      )}
    </div>
  )
}
