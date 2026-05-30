import { useCallback, useEffect, useState } from 'react'
import { api, type NtpStatus, type NtpServers } from '../lib/api'
import PageHeader from '../components/PageHeader'
import FormActions from '../components/FormActions'
import LoadingState from '../components/LoadingState'
import { ErrorBlock } from '../components/Alerts'
import { Clock } from 'lucide-react'

// Page /services/ntp : time synchronization through chrony, enabled by
// default. MurOS reads the state via timedatectl + chronyc and drops the
// server list into /etc/chrony/conf.d/muros.conf.
function Stat({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="border border-gray-200 rounded-md p-3 bg-white">
      <div className="text-[11px] uppercase tracking-wider text-gray-500">{label}</div>
      <div className={`mt-1 text-sm text-gray-900 ${mono ? 'font-mono' : ''}`}>{value}</div>
    </div>
  )
}

export default function Ntp() {
  const [status, setStatus] = useState<NtpStatus | null>(null)
  const [config, setConfig] = useState<NtpServers | null>(null)
  const [serversText, setServersText] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [working, setWorking] = useState(false)

  const reload = useCallback(() => {
    Promise.all([api.ntp.status(), api.ntp.servers()])
      .then(([s, c]) => {
        setStatus(s); setConfig(c); setServersText(c.servers.join(' ')); setError(null)
      })
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => { reload() }, [reload])
  useEffect(() => {
    const id = setInterval(() => { api.ntp.status().then(setStatus).catch(() => {}) }, 5000)
    return () => clearInterval(id)
  }, [])

  const save = async () => {
    setWorking(true)
    try {
      const list = serversText.split(/\s+/).map((s) => s.trim()).filter(Boolean)
      const c = await api.ntp.setServers(list)
      setConfig(c); setServersText(c.servers.join(' ')); setError(null)
    } catch (e) { setError((e as Error).message) } finally { setWorking(false) }
  }

  return (
    <div>
      <PageHeader
        icon={<Clock size={16} />}
        title="NTP server"
        description="Time synchronization through chrony."
        titleHelp={
          'MurOS uses chrony as the NTP daemon, enabled by default. The state '
          + 'is read from timedatectl and chronyc tracking; the server list is '
          + 'written to /etc/chrony/conf.d/muros.conf and chrony is restarted on save.'
        }
      />
      <div className="px-6 py-4 space-y-6">
        {error && <ErrorBlock message={error} onDismiss={() => setError(null)} />}

        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-gray-900">chrony synchronization</h2>
            <button className="btn-secondary" onClick={reload}>Refresh</button>
          </div>
          {status === null && <LoadingState variant="inline" />}
          {status?.available === false && (
            <div className="text-sm text-gray-800 border border-amber-300 bg-amber-50 rounded p-3">
              chrony unavailable. Check that the package is installed and the service is active.
            </div>
          )}
          {status?.available && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <Stat label="Synchronized" value={status.ntp_synchronized ? 'yes' : 'no'} />
              <Stat label="Service active" value={status.ntp_active ? 'yes' : 'no'} />
              <Stat label="Current source" value={status.ref_name || '(none)'} mono />
              <Stat label="Timezone" value={status.timezone || '-'} />
            </div>
          )}
        </section>

        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-gray-900">NTP servers</h2>
            <FormActions
              onApply={save}
              busy={working}
              dirty={!!config && serversText.trim() !== config.servers.join(' ').trim()}
              title="Save the server list and restart chrony."
            />
          </div>
          <p className="text-xs text-gray-700 mb-2">
            Space-separated server list. Written to{' '}
            <code className="font-mono">{config?.config_path || '/etc/chrony/conf.d/muros.conf'}</code>.
            The service is restarted after each save.
          </p>
          <input
            className="input w-full font-mono text-xs"
            value={serversText}
            onChange={(e) => setServersText(e.target.value)}
            placeholder="0.debian.pool.ntp.org 1.debian.pool.ntp.org 2.debian.pool.ntp.org"
          />
        </section>
      </div>
    </div>
  )
}
