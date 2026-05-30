import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, auth } from '../lib/api'
import { ErrorBlock, WarnBlock } from '../components/Alerts'

// MurOS UI is English-only. This login page used to be the only English page
// when the rest was French; the project later standardized on English.
export default function Login() {
  const nav = useNavigate()
  const [params] = useSearchParams()
  const expired = params.get('expired') === '1'
  const [username, setUsername] = useState('root')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const res = await api.auth.login(username, password)
      auth.token = res.access_token
      nav(res.must_change_password ? '/account' : '/', { replace: true })
    } catch (err) {
      setError(String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="h-full flex items-center justify-center bg-gray-50">
      <form onSubmit={onSubmit} className="w-full max-w-sm bg-white border border-gray-200 rounded-md p-6 shadow-sm">
        <div className="bg-neutral-900 rounded-md px-4 py-3 mb-6 flex items-center justify-center">
          <img src="/logo.svg" alt="MurOS" className="h-10 w-auto" />
        </div>

        <h1 className="text-lg font-semibold text-gray-900 mb-5">Sign in</h1>

        {expired && !error && (
          <div className="mb-4"><WarnBlock message="Session expired, please sign in again." /></div>
        )}
        {error && (
          <div className="mb-4"><ErrorBlock message={error} /></div>
        )}

        <div className="mb-3">
          <label className="label">Username</label>
          <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </div>

        <div className="mb-4">
          <label className="label">Password</label>
          <input type="password" className="input" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>

        <button type="submit" className="btn-primary w-full justify-center" disabled={submitting || !username || !password}>
          {submitting ? 'Signing in...' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
