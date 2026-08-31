#!/usr/bin/python3

"""
    Copyright (c) 2026 MurOS
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
     this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in the
     documentation and/or other materials provided with the distribution.

    THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
    INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
    AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
    AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
    OR CONSEQUENTIAL DAMAGES HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY.

    MurOS power savings, the Linux side of the powerd(8) checkbox in
    System: Settings: Miscellaneous.

    FreeBSD ran powerd(8), which watched the AC line and drove the CPU
    frequency itself through the cpufreq(4) sysctls. Debian has no such daemon
    and the binary was simply missing, so the setting did nothing at all.

    The kernel already does the frequency scaling here, the policy is the
    governor of the cpufreq driver, so this daemon only has to translate the
    four powerd modes into a governor, follow the AC line and reapply the
    matching mode when the machine is plugged or unplugged:

      max   maximum      the highest performance, never scale down
      min   minimum      the most power savings, never scale up
      adp   adaptive     scale with the load, favouring power savings
      hadp  hiadaptive   scale with the load, favouring responsiveness

    Which governors exist depends on the driver: intel_pstate in its active
    mode only offers performance and powersave and takes its policy from the
    energy performance preference instead, so the preference is set as well
    whenever the platform exposes it.
"""

import argparse
import glob
import os
import signal
import sys
import syslog
import time

CPUFREQ_ROOT = '/sys/devices/system/cpu'
POWER_SUPPLY_ROOT = '/sys/class/power_supply'

GOVERNORS = {
    'max': ['performance', 'schedutil', 'ondemand'],
    'min': ['powersave', 'conservative', 'schedutil'],
    'adp': ['conservative', 'schedutil', 'ondemand', 'powersave'],
    'hadp': ['schedutil', 'ondemand', 'performance'],
}

PREFERENCES = {
    'max': ['performance'],
    'min': ['power'],
    'adp': ['balance_power', 'power'],
    'hadp': ['balance_performance', 'performance'],
}


def read_file(filename):
    try:
        with open(filename) as handle:
            return handle.read().strip()
    except OSError:
        return ''


def write_file(filename, value):
    try:
        with open(filename, 'w') as handle:
            handle.write(value)
        return True
    except OSError:
        return False


def policies():
    return sorted(glob.glob(os.path.join(CPUFREQ_ROOT, 'cpu[0-9]*', 'cpufreq')))


def choose(candidates, available):
    for candidate in candidates:
        if candidate in available:
            return candidate
    return None


def on_battery():
    mains = False
    for supply in sorted(glob.glob(os.path.join(POWER_SUPPLY_ROOT, '*'))):
        if read_file(os.path.join(supply, 'type')) != 'Mains':
            continue
        mains = True
        if read_file(os.path.join(supply, 'online')) == '1':
            return False
    return mains


def apply_mode(mode):
    applied = set()
    for policy in policies():
        available = read_file(os.path.join(policy, 'scaling_available_governors')).split()
        governor = choose(GOVERNORS[mode], available)
        if governor is None:
            governor = read_file(os.path.join(policy, 'scaling_governor'))
        elif read_file(os.path.join(policy, 'scaling_governor')) != governor:
            write_file(os.path.join(policy, 'scaling_governor'), governor)
        if governor:
            applied.add(governor)

        preferences = read_file(os.path.join(policy, 'energy_performance_available_preferences')).split()
        preference = choose(PREFERENCES[mode], preferences)
        if preference is not None:
            write_file(os.path.join(policy, 'energy_performance_preference'), preference)
            applied.add(preference)
    return sorted(applied)


class Powerd:
    def __init__(self, options):
        self._options = options
        self._running = True
        self._current = None

    def stop(self, signum=None, frame=None):
        self._running = False

    def mode(self):
        if not glob.glob(os.path.join(POWER_SUPPLY_ROOT, '*')):
            return self._options.normal
        return self._options.battery if on_battery() else self._options.ac

    def run(self):
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)

        if not policies():
            syslog.syslog(syslog.LOG_WARNING, 'no cpufreq policy on this system, power savings unavailable')
            return 1

        while self._running:
            mode = self.mode()
            if mode != self._current:
                self._current = mode
                syslog.syslog(
                    syslog.LOG_NOTICE,
                    'switching to %s mode (%s)' % (mode, ', '.join(apply_mode(mode)) or 'unchanged')
                )
            time.sleep(self._options.poll_interval)
        return 0


def main():
    parser = argparse.ArgumentParser(description='Drive the CPU frequency policy from the power source')
    parser.add_argument('--ac', choices=sorted(GOVERNORS), default='hadp', help='mode while on AC power')
    parser.add_argument('--battery', choices=sorted(GOVERNORS), default='hadp', help='mode while on battery')
    parser.add_argument('--normal', choices=sorted(GOVERNORS), default='hadp', help='mode without a power source')
    parser.add_argument('--poll-interval', type=int, default=10, help='power source poll interval')
    options = parser.parse_args()

    syslog.openlog('powerd', syslog.LOG_PID, syslog.LOG_DAEMON)

    return Powerd(options).run()


if __name__ == '__main__':
    sys.exit(main())
