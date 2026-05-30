import { useEffect, useState } from 'react'
import { api, type LockoutCheck } from '../lib/api'

export default function RulesetModal({ onClose }: { onClose: () => void }) {
  const [ruleset, setRuleset] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [check, setCheck] = useState<{ ok: boolean; message: string } | null>(null)
  const [checking, setChecking] = useState(false)
  const [applying, setApplying] = useState(false)
  const [copied, setCopied] = useState(false)
  // Management-lockout guard: filled from /apply/lockout-check. When
  // blocked, the operator must tick the acknowledgement before Apply is
  // re-enabled (and the apply call carries acknowledge_lockout=true).
  const [lockout, setLockout] = useState<LockoutCheck | null>(null)
  const [ackLockout, setAckLockout] = useState(false)

  const copyRuleset = async () => {
    if (!ruleset) return
    try {
      // Modern path. Falls back to a hidden textarea on browsers without
      // the async clipboard API (rare, but the UI is served over HTTPS
      // with a snakeoil cert by default, which some browsers treat as
      // insecure context for the clipboard API).
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(ruleset)
      } else {
        const ta = document.createElement('textarea')
        ta.value = ruleset
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        document.execCommand('copy')
        document.body.removeChild(ta)
      }
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Best effort. No toast to avoid noise on rare browser quirks.
    }
  }

  useEffect(() => {
    api.rules.preview()
      .then((r) => setRuleset(r.ruleset))
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false))
    // Best-effort lockout pre-check. A failure here must never block the
    // apply flow, so we swallow errors and simply skip the warning.
    api.apply.lockoutCheck()
      .then((r) => setLockout(r))
      .catch(() => setLockout(null))
  }, [])

  const runCheck = async () => {
    setChecking(true); setCheck(null)
    try {
      const r = await api.rules.check()
      setCheck({ ok: r.ok, message: r.message })
      setRuleset(r.ruleset)
    } catch (e) {
      setCheck({ ok: false, message: (e as Error).message })
    } finally { setChecking(false) }
  }

  const doApply = async () => {
    // No window.confirm: the rollback modal (RollbackModal) already handles
    // the confirmation. If the user does not confirm, the ruleset is
    // restored automatically.
    setApplying(true)
    try {
      // Timeout omitted: the backend uses the configured apply_confirm_timeout.
      await api.apply.run(undefined, lockout?.blocked ? true : false)
      onClose()
    } catch (e) {
      setCheck({ ok: false, message: 'Apply failed: ' + (e as Error).message })
    } finally { setApplying(false) }
  }

  const lockoutBlocked = !!lockout?.blocked
  const applyDisabled = applying || loading || (lockoutBlocked && !ackLockout)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-md shadow-xl w-[min(900px,95vw)] max-h-[90vh] flex flex-col">
        <div className="px-5 py-3 border-b border-gray-200 flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-gray-900">Apply configuration</h2>
            <p className="text-xs text-gray-600 mt-0.5">
              Compile the nftables ruleset from rules, NAT, zones and interfaces. Review then apply (auto-rollback if you do not confirm in the modal that follows).
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              className="btn-secondary"
              onClick={copyRuleset}
              disabled={loading || !ruleset}
              title="Copy the compiled ruleset to the clipboard"
            >
              {copied ? 'Copied' : 'Copy'}
            </button>
            <button className="btn-ghost" onClick={onClose}>Close</button>
          </div>
        </div>

        {check && (
          <div className={`px-5 py-2 text-sm border-b border-gray-200 ${check.ok ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-800'}`}>
            <span className="font-semibold mr-2">{check.ok ? 'OK' : 'Error'}.</span>
            <span className="font-mono text-xs">{check.message}</span>
          </div>
        )}

        {lockoutBlocked && (
          <div className="px-5 py-3 text-sm border-b border-red-200 bg-red-50 text-red-800">
            <div className="font-semibold mb-1">Management lockout warning</div>
            <p className="text-xs leading-relaxed mb-2">{lockout?.message}</p>
            <label className="flex items-start gap-2 text-xs font-medium">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={ackLockout}
                onChange={(e) => setAckLockout(e.target.checked)}
              />
              <span>I understand this may block new management connections, apply anyway.</span>
            </label>
          </div>
        )}

        <div className="flex-1 overflow-auto">
          {loading && <div className="p-6 text-sm text-gray-700">Compilation...</div>}
          {error && <div className="p-4"><div className="border border-red-300 bg-red-50 text-red-800 px-3 py-2 rounded text-sm">{error}</div></div>}
          {!loading && !error && (
            <pre className="text-xs font-mono p-4 whitespace-pre">{ruleset}</pre>
          )}
        </div>

        <div className="px-5 py-3 border-t border-gray-200 flex items-center justify-between gap-2">
          <div className="text-xs text-gray-600">
            Check = <code className="font-mono">nft -c -f -</code> (read-only).
            Apply = real load + confirmation countdown (auto-rollback if not confirmed).
          </div>
          <div className="flex gap-2">
            <button className="btn-secondary" onClick={runCheck} disabled={checking || loading}>
              {checking ? 'Checking...' : 'Test the syntax'}
            </button>
            <button
              className={lockoutBlocked ? 'btn-danger' : 'btn-primary'}
              onClick={doApply}
              disabled={applyDisabled}
            >
              {applying ? 'Applying...' : lockoutBlocked ? 'Apply anyway' : 'Apply'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
