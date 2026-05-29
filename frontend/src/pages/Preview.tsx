import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import PageHeader from '../components/PageHeader'
import Modal from '../components/Modal'
import { ErrorBlock } from '../components/Alerts'
import { toast } from '../components/Toast'
import { Eye } from 'lucide-react'

export default function Preview() {
  const [ruleset, setRuleset] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [confirmApply, setConfirmApply] = useState(false)
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)

  const reload = () => {
    setLoading(true)
    api.rules.preview()
      .then((r) => { setRuleset(r.ruleset); setError(null) })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => { reload() }, [])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(ruleset)
      toast.success('Ruleset copie')
    } catch {
      toast.error('Auto-copy unavailable')
    }
  }

  const doApply = async () => {
    setApplying(true)
    setApplyError(null)
    try {
      await api.apply.run(10)
      setConfirmApply(false)
    } catch (e) {
      setApplyError(String(e))
    } finally {
      setApplying(false)
    }
  }

  return (
    <div>
      <PageHeader
        icon={<Eye size={16} />}
       
        title="Ruleset genere"
        description="Generated nftables ruleset."
        actions={
          <>
            <button className="btn-secondary" onClick={reload}>Reload</button>
            <button className="btn-secondary" onClick={copy}>Copy</button>
            <button className="btn-primary" onClick={() => setConfirmApply(true)}>Apply</button>
          </>
        }
      />

      <Modal
        open={confirmApply}
        onClose={() => setConfirmApply(false)}
        title="Apply ruleset"
        size="md"
        footer={
          <>
            <button className="btn-secondary" onClick={() => setConfirmApply(false)} disabled={applying}>Cancel</button>
            <button className="btn-primary" onClick={doApply} disabled={applying}>
              {applying ? 'Applying...' : 'Apply now'}
            </button>
          </>
        }
      >
        {applyError && (
          <ErrorBlock message={applyError} />
        )}
        <p className="text-sm text-gray-800 mb-2">
          The ruleset is about to be pushed to the firewall. A 10-second timer will start:
          if you do not confirm the apply via the bottom banner, the previous ruleset will be restored
          automatically.
        </p>
        <p className="text-xs text-gray-700">
          If you are developing on your local machine, apply runs in dry-run mode (nothing is actually applied).
        </p>
      </Modal>
      <div className="px-6 py-4">
        {error && (
          <ErrorBlock message={error} />
        )}
        <pre className="bg-gray-50 border border-gray-200 rounded p-4 text-xs font-mono leading-relaxed overflow-x-auto whitespace-pre text-gray-800">
          {loading ? 'Loading...' : ruleset}
        </pre>
      </div>
    </div>
  )
}
