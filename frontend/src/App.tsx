// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 MurOS contributors.
import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import Layout from './components/Layout'
// Dashboard fusionne avec Monitoring : un seul ecran de supervision.
import Rules from './pages/Rules'
import Services from './pages/Services'
import Preview from './pages/Preview'
import Nat from './pages/Nat'
import Zones from './pages/Zones'
import Network from './pages/Network'
import RoutesPage from './pages/Routes'
import WanPage from './pages/Wan'
import Logs from './pages/Logs'
import Monitoring from './pages/Monitoring'
import System from './pages/System'
import HA from './pages/HA'
import WireGuard from './pages/WireGuard'
import IPsec from './pages/IPsec'
import Notifications from './pages/Notifications'
import SNMP from './pages/SNMP'
import SSH from './pages/SSH'
import HttpAccess from './pages/HttpAccess'
import Diagnostic from './pages/Diagnostic'
import DhcpPage from './pages/Dhcp'
import DnsPage from './pages/Dns'
import Login from './pages/Login'
import { auth, setUnauthorizedHandler } from './lib/api'
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
        <Route element={<RequireAuth><Layout /></RequireAuth>}>
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
          {/* DNS sub-routes for bookmarks: server, records. */}
          <Route path="/services/dns/:tab" element={<DnsPage />} />
          <Route path="/access/http" element={<HttpAccess />} />
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
