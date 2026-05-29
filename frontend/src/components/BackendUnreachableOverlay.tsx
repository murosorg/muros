// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 MurOS contributors.
//
// Quiet "Reconnecting..." overlay shown when the API stops answering.
// Typical case: the operator clicks "Install MurOS update" and the
// muros-backend systemd unit restarts a few seconds later. Without this
// overlay, every in-flight XHR fails with a 502 from nginx and the UI
// floods the operator with red "502 Bad Gateway" toasts that suggest
// they just bricked their firewall.
//
// Wiring: the global request() wrapper in lib/api.ts dispatches a
// `muros:backend-down` event on 502/503/504 or network-level failures,
// and `muros:backend-up` on the next successful round-trip. This
// component listens to both, waits SHOW_AFTER_MS to filter transient
// blips, then renders a calm full-screen card. While visible it polls
// /api/health every POLL_INTERVAL_MS until the backend comes back.
//
// Visual language matches the static nginx 503 page served by
// /etc/nginx/html/muros-503.html for top-level navigation: small
// pulsing amber dot, neutral copy, no error iconography.

import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'

// Delay before the overlay actually appears. One failed poll during a
// reload should not flash a full-screen card.
const SHOW_AFTER_MS = 1500

// Health probe interval while the overlay is visible.
const POLL_INTERVAL_MS = 2000

export default function BackendUnreachableOverlay() {
  const [visible, setVisible] = useState(false)
  const [sinceMs, setSinceMs] = useState(0)
  const showTimer = useRef<number | null>(null)
  const downSince = useRef<number | null>(null)
  // Mirrors `visible` so the down-event listener (registered once,
  // closes over the initial state) can early-exit when the overlay is
  // already shown without re-arming the reveal timer.
  const visibleRef = useRef(false)

  useEffect(() => {
    const onDown = () => {
      if (downSince.current === null) downSince.current = Date.now()
      if (showTimer.current !== null || visibleRef.current) return
      showTimer.current = window.setTimeout(() => {
        showTimer.current = null
        visibleRef.current = true
        setVisible(true)
      }, SHOW_AFTER_MS)
    }
    const onUp = () => {
      if (showTimer.current !== null) {
        window.clearTimeout(showTimer.current)
        showTimer.current = null
      }
      downSince.current = null
      visibleRef.current = false
      setVisible(false)
      setSinceMs(0)
    }
    window.addEventListener('muros:backend-down', onDown)
    window.addEventListener('muros:backend-up', onUp)
    return () => {
      window.removeEventListener('muros:backend-down', onDown)
      window.removeEventListener('muros:backend-up', onUp)
      if (showTimer.current !== null) window.clearTimeout(showTimer.current)
    }
  }, [])

  // Active health probe while the overlay is visible. A successful
  // /api/health round-trip fires `muros:backend-up` through the global
  // request() wrapper, which closes the overlay automatically.
  useEffect(() => {
    if (!visible) return
    const tick = () => {
      api.health().catch(() => { /* still down, keep waiting */ })
      if (downSince.current !== null) {
        setSinceMs(Date.now() - downSince.current)
      }
    }
    tick()
    const id = window.setInterval(tick, POLL_INTERVAL_MS)
    return () => window.clearInterval(id)
  }, [visible])

  if (!visible) return null

  const seconds = Math.floor(sinceMs / 1000)
  const longWait = seconds >= 60

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed inset-0 z-[9999] flex items-center justify-center bg-gray-900/40 backdrop-blur-sm p-4"
    >
      <div className="bg-white border border-gray-200 rounded-lg shadow-lg max-w-md w-full p-6">
        <div className="flex items-center gap-2 mb-3">
          <span className="inline-block w-2 h-2 bg-amber-500 rounded-full animate-pulse" />
          <h2 className="text-base font-medium text-gray-900 m-0">
            Reconnecting to MurOS
          </h2>
        </div>
        <p className="text-sm text-gray-600 m-0 mb-2">
          The administration backend is not answering. It is probably
          restarting after an update or applying a configuration change.
        </p>
        <p className="text-sm text-gray-700 m-0 mb-2">
          <span className="font-medium">Traffic forwarding is not affected.</span>{' '}
          Firewall rules, NAT, DHCP, DNS, WireGuard and IPsec tunnels keep
          running normally. Only this admin interface is momentarily
          unreachable.
        </p>
        <p className="text-sm text-gray-600 m-0">
          This window will close on its own as soon as the connection is
          back. Nothing to do, no need to reload.
        </p>

        <div className="mt-4 pt-3 border-t border-gray-100 flex items-center justify-between text-xs text-gray-500">
          <span>Waiting {seconds}s</span>
          {longWait && (
            <code className="font-mono bg-gray-50 border border-gray-200 px-1.5 py-0.5 rounded text-gray-600">
              systemctl status muros-backend
            </code>
          )}
        </div>
      </div>
    </div>
  )
}
