# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Conf generators for the network services (DHCP, DNS).

Each module here reads the DB and rewrites a conf file under
/etc/kea/ or /etc/unbound/unbound.conf.d/, then triggers a
systemctl reload. A single source of truth: the MurOS DB.
"""
