import { useEffect, useState } from 'react'
import { api, type AdminUser, type UsersList, type User } from '../lib/api'
import PageHeader from '../components/PageHeader'
import Toggle from '../components/Toggle'
import { useConfirm } from '../components/ConfirmModal'
import { ErrorBlock, SuccessBlock } from '../components/Alerts'
import { Users as UsersIcon } from 'lucide-react'

// Access > Users : root grants web UI access to local Linux accounts.
// The web UI and SSH share the system PAM stack, so any Linux account
// could authenticate; this page decides which ones are actually allowed
// into the UI. Only root (and accounts it promotes to administrator)
// can reach this page.
export default function Users() {
  const [data, setData] = useState<UsersList | null>(null)
  const [me, setMe] = useState<User | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [grantName, setGrantName] = useState('')
  const [grantAdmin, setGrantAdmin] = useState(false)
  const { confirm, ConfirmHost } = useConfirm()

  const reload = async () => {
    try {
      const [list, who] = await Promise.all([api.users.list(), api.auth.me()])
      setData(list)
      setMe(who)
      if (!grantName && list.grantable_accounts.length > 0) {
        setGrantName(list.grantable_accounts[0])
      }
    } catch (e) {
      setError((e as Error).message)
    }
  }

  useEffect(() => { void reload() }, [])

  const isRoot = (u: AdminUser) => u.username === 'root'
  const isSelf = (u: AdminUser) => me != null && u.id === me.id
  const locked = (u: AdminUser) => isRoot(u) || isSelf(u)

  const grant = async () => {
    if (!grantName) return
    setBusy(true); setError(null); setMessage(null)
    try {
      await api.users.grant(grantName, grantAdmin)
      setMessage(`Access granted to ${grantName}.`)
      setGrantName('')
      setGrantAdmin(false)
      await reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const setAccess = async (u: AdminUser, value: boolean) => {
    setBusy(true); setError(null); setMessage(null)
    try {
      await api.users.update(u.id, { ui_access: value })
      await reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const setAdmin = async (u: AdminUser, value: boolean) => {
    setBusy(true); setError(null); setMessage(null)
    try {
      await api.users.update(u.id, { is_admin: value })
      await reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const remove = async (u: AdminUser) => {
    const ok = await confirm({
      title: `Remove ${u.username}?`,
      message: 'This revokes web UI access and removes the MurOS record. '
        + 'The Linux account and its password are left untouched.',
      confirmLabel: 'Remove',
    })
    if (!ok) return
    setBusy(true); setError(null); setMessage(null)
    try {
      await api.users.remove(u.id)
      setMessage(`${u.username} removed.`)
      await reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const fmtDate = (s: string | null) => (s ? new Date(s).toLocaleString() : 'Never')

  return (
    <div>
      <PageHeader
        title="Users"
        description="Grant or revoke web UI access for local Linux accounts."
        icon={<UsersIcon size={18} />}
        titleHelp={
          'The web UI and SSH share the same Linux accounts (PAM). Only root '
          + 'is allowed into the UI by default. Grant access here to let other '
          + 'system accounts sign in. Administrators can manage users; standard '
          + 'users can use every other page but not this one.'
        }
      />
      <div className="px-6 py-4 space-y-6">
        {error && <ErrorBlock message={error} onDismiss={() => setError(null)} />}
        {message && <SuccessBlock message={message} onDismiss={() => setMessage(null)} />}

        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-gray-900">Web UI accounts</h2>
          </div>
          <div className="border border-gray-200 rounded-md overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-xs uppercase tracking-wider text-gray-600">
                <tr>
                  <th className="text-left font-semibold px-3 py-2">Account</th>
                  <th className="text-left font-semibold px-3 py-2">Administrator</th>
                  <th className="text-left font-semibold px-3 py-2">Web UI access</th>
                  <th className="text-left font-semibold px-3 py-2">Last login</th>
                  <th className="text-right font-semibold px-3 py-2">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {(data?.users || []).map((u) => (
                  <tr key={u.id} className="hover:bg-gray-50">
                    <td className="px-3 py-2">
                      <span className="font-mono text-gray-900">{u.username}</span>
                      {isRoot(u) && (
                        <span className="ml-2 text-[10px] uppercase tracking-wider bg-gray-100 text-gray-700 border border-gray-200 px-1.5 py-0.5 rounded">
                          default admin
                        </span>
                      )}
                      {isSelf(u) && !isRoot(u) && (
                        <span className="ml-2 text-[10px] uppercase tracking-wider bg-gray-100 text-gray-700 border border-gray-200 px-1.5 py-0.5 rounded">
                          you
                        </span>
                      )}
                      {!u.exists_on_system && (
                        <span className="ml-2 text-[10px] uppercase tracking-wider bg-amber-50 text-amber-800 border border-amber-200 px-1.5 py-0.5 rounded">
                          no system account
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <Toggle
                        checked={u.is_admin}
                        disabled={busy || locked(u) || !u.ui_access}
                        onChange={() => void setAdmin(u, !u.is_admin)}
                      />
                    </td>
                    <td className="px-3 py-2">
                      <Toggle
                        checked={u.ui_access}
                        disabled={busy || locked(u)}
                        onChange={() => void setAccess(u, !u.ui_access)}
                      />
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-gray-700">{fmtDate(u.last_login)}</td>
                    <td className="px-3 py-2 text-right">
                      <button
                        className="btn-ghost py-1 text-red-700 hover:text-red-800"
                        disabled={busy || locked(u)}
                        onClick={() => void remove(u)}
                        title={locked(u) ? 'This account cannot be removed' : 'Remove this account'}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
                {data && data.users.length === 0 && (
                  <tr><td colSpan={5} className="px-3 py-6 text-center text-gray-600">No account yet.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section>
          <h2 className="text-sm font-semibold text-gray-900 mb-3">Grant access</h2>
          <div className="border border-gray-200 rounded-md p-4 space-y-3">
            {data && data.grantable_accounts.length === 0 ? (
              <p className="text-sm text-gray-700">
                Every local Linux login account already has a record. Create a new
                system account on the firewall first, then grant it access here.
              </p>
            ) : (
              <div className="flex flex-wrap items-end gap-4">
                <label className="block">
                  <span className="block text-xs font-semibold text-gray-700 mb-1">Linux account</span>
                  <select
                    className="border border-gray-300 rounded px-2 py-1.5 text-sm font-mono min-w-[200px]"
                    value={grantName}
                    disabled={busy}
                    onChange={(e) => setGrantName(e.target.value)}
                  >
                    {(data?.grantable_accounts || []).map((n) => (
                      <option key={n} value={n}>{n}</option>
                    ))}
                  </select>
                </label>
                <label className="inline-flex items-center gap-2 text-sm text-gray-800 pb-2">
                  <Toggle checked={grantAdmin} disabled={busy} onChange={() => setGrantAdmin(!grantAdmin)} />
                  Administrator
                </label>
                <button className="btn-primary" disabled={busy || !grantName} onClick={() => void grant()}>
                  Grant access
                </button>
              </div>
            )}
            <p className="text-xs text-gray-600">
              Granted accounts sign in with their Linux password (the same one used
              for SSH). Administrators can manage users on this page; standard users
              cannot.
            </p>
          </div>
        </section>
      </div>
      <ConfirmHost />
    </div>
  )
}
