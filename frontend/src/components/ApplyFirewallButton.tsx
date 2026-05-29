import { useEffect, useState } from 'react'
import { api, type FirewallPending } from '../lib/api'

/**
 * Apply button shared by Filter rules / NAT / Zones / Services pages.
 *
 * Polls /api/firewall/pending every 3s. The button is disabled when
 * the kernel ruleset matches the DB (no pending change). When dirty,
 * an orange dot decorates the button and the count is exposed via the
 * native tooltip so the admin knows what is going to change.
 *
 * The click handler is delegated to the parent (typically opens the
 * ruleset preview modal which then calls api.apply.run()). This keeps
 * the existing confirmation flow intact.
 */
export default function ApplyFirewallButton({
  onClick,
  onView,
}: {
  onClick: () => void
  /**
   * Optional. If provided, a secondary "View config" button is rendered to
   * the left of Apply. It opens the ruleset preview (same modal as Apply)
   * regardless of pending state, so the admin can inspect the compiled
   * nftables ruleset at any time. Use this on pages that already show
   * the RulesetModal on Apply click (Rules/NAT/Zones/Services).
   */
  onView?: () => void
}) {
  const [pending, setPending] = useState<FirewallPending>({
    rules: 0, nat: 0, zones: 0, total: 0,
  })

  const reload = async () => {
    try {
      const r = await api.apply.pending()
      setPending(r)
    } catch {
      // Silent fail. The button keeps its previous state; the global
      // error toast handles backend outages.
    }
  }

  useEffect(() => {
    void reload()
    const id = setInterval(reload, 3000)
    return () => clearInterval(id)
  }, [])

  const { total, rules, nat, zones } = pending
  const dirty = total > 0
  const tooltip = dirty
    ? `${total} pending change(s):\n- ${rules} rule(s)\n- ${nat} NAT rule(s)\n- ${zones} zone(s)`
    : 'No pending changes (DB matches the kernel ruleset)'

  return (
    <div className="flex items-center gap-2">
      {onView && (
        <button
          className="btn-secondary"
          onClick={onView}
          title="Inspect the compiled nftables ruleset (read-only). Available even when there are no pending changes."
        >
          View config
        </button>
      )}
      <button
        className="btn-apply relative"
        onClick={onClick}
        disabled={!dirty}
        title={tooltip}
      >
        {dirty && (
          <span
            className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-orange-500 ring-2 ring-white"
            aria-hidden="true"
          />
        )}
        Apply
      </button>
    </div>
  )
}
