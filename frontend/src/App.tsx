// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 MurOS contributors.
import { lazy, useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import Layout from './components/Layout'
// Page components are code-split with React.lazy so the initial bundle
// stays small: each screen is fetched only when the operator navigates
// to it. This matters on the modest hardware MurOS targets. Login and
// the Layout shell are kept eager since they are on the critical path
// of the very first paint.
// Dashboard fusionne avec Monitoring : un seul ecran de supervision.
import Login from './pages/Login'
const Setup = lazy(() => import('./pages/Setup'))
const Rules = lazy(() => import('./pages/Rules'))
const Services = lazy(() => import('./pages/Services'))
const Preview = lazy(() => import('./pages/Preview'))
const Nat = lazy(() => import('./pages/Nat'))
const Zones = lazy(() => import('./pages/Zones'))
const Network = lazy(() => import('./pages/Network'))
const RoutesPage = lazy(() => import('./pages/Routes'))
const WanPage = lazy(() => import('./pages/Wan'))
const Logs = lazy(() => import('./pages/Logs'))
const Monitoring = lazy(() => import('./pages/Monitoring'))
const System = lazy(() => import('./pages/System'))
const HA = lazy(() => import('./pages/HA'))
const WireGuard = lazy(() => import('./pages/WireGuard'))
const IPsec = lazy(() => import('./pages/IPsec'))
const Notifications = lazy(() => import('./pages/Notifications'))
const SNMP = lazy(() => import('./pages/SNMP'))
const SSH = lazy(() => import('./pages/SSH'))
const HttpAccess = lazy(() => import('./pages/HttpAccess'))
const Diagnostic = lazy(() => import('./pages/Diagnostic'))
const DhcpPage = lazy(() => import('./pages/Dhcp'))
const DnsPage = lazy(() => import('./pages/Dns'))
const NtpPage = lazy(() => import('./pages/Ntp'))
const UsersPage = lazy(() => import('./pages/Users'))
import { api, auth, setUnauthorizedHandler } from './lib/api'
import { ToastHost } from './components/Toast'
import BackendUnreachableOverlay from './components/BackendUnreachableOverlay'

function RequireAuth({ children }: { children: React.ReactNode }) {
  const nav = useNavigate()
  const [loggedIn, setLoggedIn] = useState(auth.isLoggedIn())
  useEffect(() => {
    setUnauthorizedHandler((expired) => {
      setLoggedIn(false)
      // ?expired=1 declenche un message "Session expiree" sur la page de
      // login pour que l'utilisateur comprenne pourquoi il est ejecte. On
      // ne l'ajoute QUE si une session valide a deja existe ce chargement,
      // sinon un token perime laisse en localStorage afficherait un
      // message trompeur au premier acces.
      nav(expired ? '/login?expired=1' : '/login', { replace: true })
    })
  }, [nav])
  if (!loggedIn) return <Navigate to="/login" replace />
  return <>{children}</>
}

// Gate the app behind the first-boot wizard. Until the operator has
// assigned the WAN/LAN interfaces, every authenticated route redirects to
// /setup. Checked once per mount; failures fail open (show the app) so a
// transient API error never bricks access to a configured box.
function RequireSetup({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<'loading' | 'ok' | 'setup'>('loading')
  useEffect(() => {
    api.setup.state()
      .then((s) => setState(s.completed ? 'ok' : 'setup'))
      .catch(() => setState('ok'))
  }, [])
  if (state === 'loading') return null
  if (state === 'setup') return <Navigate to="/setup" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <BrowserRouter>
      <ToastHost />
      {/* Renders a quiet "Reconnecting..." card whenever the backend
          stops answering (typical during a MurOS update). Replaces the
          red "502 Bad Gateway" toast spam with a calm overlay that
          dismisses itself as soon as the backend is back. */}
      <BackendUnreachableOverlay />
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/setup" element={<RequireAuth><Setup /></RequireAuth>} />
        <Route element={<RequireAuth><RequireSetup><Layout /></RequireSetup></RequireAuth>}>
          <Route path="/" element={<Monitoring />} />
          <Route path="/firewall/rules" element={<Rules />} />
          <Route path="/firewall/services" element={<Services />} />
          <Route path="/firewall/preview" element={<Preview />} />
          <Route path="/nat" element={<Nat />} />
          <Route path="/network" element={<Network />} />
          <Route path="/routes" element={<RoutesPage />} />
          <Route path="/wan" element={<WanPage />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/monitoring" element={<Navigate to="/" replace />} />
          <Route path="/zones" element={<Zones />} />
          <Route path="/system" element={<System />} />
          {/* Sous-routes System : permettent les bookmarks et le back-button browser.
              Le tab est extrait via useParams cote composant. */}
          <Route path="/system/:tab" element={<System />} />
          <Route path="/ha" element={<HA />} />
          <Route path="/vpn/wireguard" element={<WireGuard />} />
          {/* WireGuard sub-routes for bookmarks: server, peers. */}
          <Route path="/vpn/wireguard/:tab" element={<WireGuard />} />
          <Route path="/vpn/ipsec" element={<IPsec />} />
          {/* Sous-routes IPsec pour bookmarks (connections, certificates, users). */}
          <Route path="/vpn/ipsec/:tab" element={<IPsec />} />
          <Route path="/notifications" element={<Notifications />} />
          <Route path="/snmp" element={<SNMP />} />
          <Route path="/ssh" element={<SSH />} />
          <Route path="/diagnostic" element={<Diagnostic />} />
          <Route path="/services/dhcp" element={<DhcpPage />} />
          <Route path="/services/dns" element={<DnsPage />} />
          <Route path="/services/ntp" element={<NtpPage />} />
          <Route path="/system/time" element={<Navigate to="/services/ntp" replace />} />
          {/* DNS sub-routes for bookmarks: server, records. */}
          <Route path="/services/dns/:tab" element={<DnsPage />} />
          <Route path="/access/http" element={<HttpAccess />} />
          <Route path="/access/users" element={<UsersPage />} />
          {/* Back-compat: ancien path /account redirige vers /access/http. */}
          <Route path="/account" element={<Navigate to="/access/http" replace />} />
          <Route path="/tls" element={<Navigate to="/access/http" replace />} />
          {/* /ssh garde son path pour ne pas casser les liens, alias /access/ssh ajoute. */}
          <Route path="/access/ssh" element={<Navigate to="/ssh" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
