#!/usr/bin/python3

"""
    Copyright (c) 2026 MurOS
    All rights reserved.

    Placeholder for FreeBSD kernel statistics (mbuf, netisr, bpf) that have no
    direct Linux equivalent. Emits an empty JSON object so the GUI widgets render
    cleanly instead of failing on a missing FreeBSD-only netstat command.
"""
print('{}')
