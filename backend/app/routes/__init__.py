# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Package routes : re-exporte tous les APIRouter de l'API MurOS."""
from .auth import auth_router
from .users import users_router
from .network_fw import (
    zones_router, interfaces_router, network_router,
    firewall_router, nat_router, routes_router,
)
from .system_ops import (
    logs_router, metrics_router, backups_router, ntp_router, dns_router,
    updates_router, hardening_router, backup_remote_router,
    pending_router, pending_apply_router, system_settings_router,
)
from .ha import ha_router
from .tls_ssh import tls_router, ssh_router
from .diag_http import diag_router, http_router
from .system_actions import system_actions_router
from .ha_sync_pub import ha_sync_pub_router
from .vpn import wireguard_router, ipsec_router
from .notif import notifications_router, snmp_router
from .firewall_groups import service_groups_router, address_groups_router
from .wan import wan_router
from .services import dhcp_router, dns_services_router
from .service_apply import service_apply_router
from .setup import setup_router
from .ipv6 import ra_router
from .qos import qos_router
from .syslog import syslog_router
from .dyndns import dyndns_router

__all__ = [
    'auth_router',
    'users_router',
    'zones_router', 'interfaces_router', 'network_router',
    'firewall_router', 'nat_router', 'routes_router',
    'logs_router', 'metrics_router', 'backups_router',
    'ntp_router', 'dns_router', 'updates_router',
    'hardening_router', 'backup_remote_router',
    'pending_router', 'pending_apply_router', 'system_settings_router',
    'ha_router',
    'tls_router', 'ssh_router',
    'diag_router', 'http_router',
    'system_actions_router',
    'ha_sync_pub_router',
    'wireguard_router', 'ipsec_router',
    'notifications_router', 'snmp_router',
    'service_groups_router', 'address_groups_router',
    'wan_router',
    'dhcp_router', 'dns_services_router',
    'service_apply_router',
    'setup_router',
    'ra_router',
    'qos_router',
    'syslog_router',
    'dyndns_router',
]
