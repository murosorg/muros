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
  // Two-factor step: when the account has TOTP enabled the password step
  // returns an mfa_token instead of an access token, and we ask for the
  // 6-digit code before completing the login.
  const [mfaToken, setMfaToken] = useState<string | null>(null)
  const [code, setCode] = useState('')

  const finish = (res: { access_token?: string; must_change_password: boolean }) => {
    auth.token = res.access_token ?? null
    nav(res.must_change_password ? '/account' : '/', { replace: true })
  }

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const res = await api.auth.login(username, password)
      if (res.mfa_required && res.mfa_token) {
        setMfaToken(res.mfa_token)
      } else {
        finish(res)
      }
    } catch (err) {
      setError(String(err))
    } finally {
      setSubmitting(false)
    }
  }

  const onVerify = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!mfaToken) return
    setSubmitting(true)
    setError(null)
    try {
      finish(await api.auth.verifyMfa(mfaToken, code))
    } catch (err) {
      setError(String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="h-full flex items-center justify-center bg-gray-50">
      <form
        onSubmit={mfaToken ? onVerify : onSubmit}
        className="w-full max-w-sm bg-white border border-gray-200 rounded-md p-6 shadow-sm"
      >
        <div className="bg-neutral-900 rounded-md px-4 py-3 mb-6 flex items-center justify-center">
          <img src="/logo.svg" alt="MurOS" className="h-10 w-auto" />
        </div>

        <h1 className="text-lg font-semibold text-gray-900 mb-5">
          {mfaToken ? 'Two-factor verification' : 'Sign in'}
        </h1>

        {expired && !error && (
          <div className="mb-4"><WarnBlock message="Session expired, please sign in again." /></div>
        )}
        {error && (
          <div className="mb-4"><ErrorBlock message={error} /></div>
        )}

        {!mfaToken ? (
          <>
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
          </>
        ) : (
          <>
            <p className="text-sm text-gray-700 mb-3">
              Enter the 6-digit code from your authenticator app.
            </p>
            <div className="mb-4">
              <label className="label">Verification code</label>
              <input
                className="input tracking-widest text-center font-mono"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="000000"
                autoFocus
              />
            </div>
            <button type="submit" className="btn-primary w-full justify-center" disabled={submitting || code.length < 6}>
              {submitting ? 'Verifying...' : 'Verify'}
            </button>
          </>
        )}
      </form>
    </div>
  )
}
