import { ReactNode } from 'react'
import HelpTooltip from './HelpTooltip'
import Toggle from './Toggle'

type Props = {
  title: string
  description?: string
  actions?: ReactNode
  // Lucide icon component. Affichee dans un carre 32x32 a gauche du titre.
  // Donne un repere visuel constant entre les pages (Network, Firewall, VPN...).
  icon?: ReactNode
  // Tooltip avance affiche derriere un "?" a cote du titre. Utile pour
  // sortir du gros pave d'aide hors du chrome de la page tout en gardant
  // l'info accessible. Le `description` reste pour la phrase courte sous
  // le titre, `titleHelp` est reserve aux details techniques.
  titleHelp?: string
  // Inline runtime status rendered between the description and the
  // actions slot. Used on service pages (DHCP, DNS server, WireGuard,
  // IPsec, HA, SNMP, SSH) to surface dot + state + version directly in
  // the page banner instead of in a dedicated "Live status" section.
  status?: ReactNode
  // Persistent enable/disable switch rendered immediately LEFT of the
  // status pill on service pages. The handler is expected to ask the
  // operator for confirmation (these actions stop daemons) before
  // flipping the underlying flag and applying.
  serviceEnabled?: boolean
  onServiceEnabledChange?: () => void | Promise<void>
  serviceToggleBusy?: boolean
  serviceToggleTitle?: string
}

export default function PageHeader({
  title, description, actions, icon, titleHelp, status,
  serviceEnabled, onServiceEnabledChange, serviceToggleBusy,
  serviceToggleTitle,
}: Props) {
  const hasToggle = onServiceEnabledChange !== undefined && serviceEnabled !== undefined
  return (
    <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between gap-4 bg-white">
      <div className="flex items-center gap-3 min-w-0">
        {icon && (
          <div className="shrink-0 inline-flex items-center justify-center w-8 h-8 rounded bg-gray-100 text-gray-700">
            {icon}
          </div>
        )}
        <div className="min-w-0">
          <h1 className="text-lg font-semibold text-gray-900 leading-tight flex items-center gap-2">
            <span>{title}</span>
            {titleHelp && <HelpTooltip text={titleHelp} />}
          </h1>
          {description && <p className="text-xs text-gray-700 mt-0.5 truncate">{description}</p>}
        </div>
      </div>
      {(status || actions || hasToggle) && (
        <div className="flex items-center gap-3 shrink-0">
          {hasToggle && (
            <label
              className="inline-flex items-center gap-2 text-xs select-none"
              title={
                serviceToggleTitle ??
                (serviceEnabled
                  ? 'Service enabled. Click to ask for confirmation before stopping it.'
                  : 'Service disabled. Click to ask for confirmation before starting it.')
              }
            >
              <Toggle
                checked={!!serviceEnabled}
                disabled={!!serviceToggleBusy}
                onChange={() => { void onServiceEnabledChange?.() }}
              />
              {serviceToggleBusy && (
                <span
                  className="inline-block w-3 h-3 rounded-full border-2 border-gray-300 border-t-gray-700 animate-spin"
                  aria-hidden="true"
                />
              )}
              <span className={serviceEnabled ? 'text-gray-800' : 'text-gray-600'}>
                {serviceToggleBusy
                  ? (serviceEnabled ? 'Stopping...' : 'Starting...')
                  : (serviceEnabled ? 'Enabled' : 'Disabled')}
              </span>
            </label>
          )}
          {status}
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}
    </div>
  )
}
