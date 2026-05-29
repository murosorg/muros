# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Generators de conf pour les network services (DHCP, DNS).

each module here lit la DB et reecrit un fichier de conf dans
/etc/dnsmasq.d/ ou /etc/unbound/unbound.conf.d/, puis declenche un
systemctl reload. Une seule source de verite : la DB MurOS.
"""
