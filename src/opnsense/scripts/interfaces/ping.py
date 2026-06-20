#!/usr/local/bin/python3

"""
    Copyright (c) 2023 Ad Schellevis <ad@opnsense.org>
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
    OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
    SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
    INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
    CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
    ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
    POSSIBILITY OF SUCH DAMAGE.

"""

TEMP_DIR = '/tmp/ping/'

import argparse
import glob
import re
import subprocess
import os
import ujson
import sys
sys.path.insert(0, "/usr/local/opnsense/site-python")
from log_helper import reverse_log_reader


def ping_pids(jobid):
    """ return the pid of the running ping job, if any

        On Debian the ping job is tracked through the pid file written when it
        was started; we verify the pid is still alive and really belongs to a
        ping process (guards against pid reuse). The FreeBSD version matched a
        daemon(8) process title with pgrep, which is not available here.

        :param jobid: job uuid number
        :return: list of pids (as strings)
    """
    pids = []
    pidfile = "%s%s.pid" % (TEMP_DIR, jobid)
    if os.path.isfile(pidfile):
        try:
            with open(pidfile, 'r') as f_in:
                pid = f_in.read().strip()
        except OSError:
            return pids
        if pid.isdigit():
            try:
                with open('/proc/%s/comm' % pid, 'r') as f_comm:
                    if f_comm.read().strip() == 'ping':
                        pids.append(pid)
            except OSError:
                # process is gone
                pass
    return pids


def load_settings(filename):
    try:
        return ujson.load(open(filename, 'r'))
    except ValueError:
        return {}


def read_latest_stats(filename):
    result = {
        'loss': None,
        'send': None,
        'received': None,
        'min': None,
        'max': None,
        'avg': None,
        'std-dev': None,
        'last_error': None
    }
    if not os.path.isfile(filename):
        return result

    # The log is read in reverse, so the most recent run is seen first: the rtt
    # summary line, then the packet-loss line, then the "--- ping statistics ---"
    # header that ends the run. iputils (Debian) emits:
    #   N packets transmitted, M received, P% packet loss, time Tms
    #   rtt min/avg/max/mdev = a/b/c/d ms
    # Two iputils stat shapes are handled. Final block (run end or SIGINT):
    #   N packets transmitted, M received, P% packet loss, time Tms
    #   rtt min/avg/max/mdev = a/b/c/d ms
    # Intermediate single line (SIGQUIT, used by the live "list" action):
    #   N/M packets, P% loss, min/avg/ewma/max = a/b/c/d ms
    have_loss = False
    have_rtt = False
    for entry in reverse_log_reader(filename):
        line = entry['line'].strip()
        if line == '':
            continue
        if result['last_error'] is None:
            # error lines are "<argv0>: <message>"; argv0 is the ping binary,
            # which carries its full path on Debian (e.g. "/bin/ping: ...")
            err = re.match(r'(?:\S*/)?ping6?:\s*(.*)', line)
            if err:
                result['last_error'] = err.group(1).strip()
                continue
        if not have_rtt and '=' in line and '/' in line.split('=', 1)[0]:
            seg = line.split('=', 1)[1].strip().split()
            if seg and seg[0].count('/') == 3:
                try:
                    vals = [float(p) for p in seg[0].split('/')]
                    result['min'] = vals[0]
                    result['avg'] = vals[1]
                    if 'ewma' in line:
                        # intermediate order is min/avg/ewma/max, no deviation
                        result['max'] = vals[3]
                    else:
                        # final order is min/avg/max/mdev
                        result['max'] = vals[2]
                        result['std-dev'] = vals[3]
                    have_rtt = True
                except (ValueError, IndexError):
                    pass
        if not have_loss:
            matched = re.search(
                r'(\d+) packets transmitted, (\d+)(?: packets)? received.*?([\d.]+)% packet loss',
                line
            )
            if matched is None:
                matched = re.search(r'(\d+)/(\d+) packets, ([\d.]+)% loss', line)
            if matched:
                result['send'] = int(matched.group(1))
                result['received'] = int(matched.group(2))
                result['loss'] = "%0.2f %%" % float(matched.group(3))
                have_loss = True
        if have_loss and have_rtt:
            break
        if 'ping statistics' in line and have_loss:
            break
    return result


if __name__ == '__main__':
    result = dict()
    parser = argparse.ArgumentParser()
    parser.add_argument('--job', help='job id', default=None)
    parser.add_argument('action', help='action to perfom', choices=['list', 'start', 'stop', 'remove', 'view'])
    cmd_args = parser.parse_args()

    all_jobs = {}
    if os.path.exists(TEMP_DIR):
        for filename in glob.glob("%s*.json" % TEMP_DIR):
            all_jobs[os.path.basename(filename).split('.')[0]] = filename

    if cmd_args.action == 'list':
        result['jobs'] = []
        result['status'] = 'ok'
        for jobid in all_jobs:
            this_pids = ping_pids(jobid)
            if len(this_pids) > 0:
                # iputils ping prints intermediate statistics on SIGQUIT
                # (FreeBSD used SIGINFO, which does not exist on Linux)
                with open("%s.pid" % all_jobs[jobid][:-5], 'r') as f_in:
                    subprocess.run(['kill', '-s', 'QUIT', f_in.read().strip()])
            settings = load_settings(all_jobs[jobid])
            settings['id'] = jobid
            settings['status'] = "running" if len(this_pids) > 0 else "stopped"
            # merge stats
            settings.update(read_latest_stats("%s.log" % all_jobs[jobid][:-5]))
            result['jobs'].append(settings)
    elif cmd_args.action == 'start' and cmd_args.job in all_jobs:
        this_pids = ping_pids(cmd_args.job)
        if len(this_pids) > 0:
            result['status'] = 'failed'
            result['status_msg'] = 'already active (pids: %s)' % ','.join(this_pids)
        else:
            result['status'] = 'ok'
            settings = load_settings(all_jobs[cmd_args.job])
            log_target = "%s%s.log" % (TEMP_DIR, cmd_args.job)
            pid_target = "%s%s.pid" % (TEMP_DIR, cmd_args.job)
            args = [
                '/bin/ping',
                '-c', '86400', # hard limit: stop after 1 day
                '-4' if settings.get('fam', 'ip') == 'ip' else '-6'
            ]
            if settings.get('source_address', '') != '':
                # FreeBSD used -S <src>; iputils selects the source with -I
                args.append('-I')
                args.append(settings['source_address'])
            if settings.get('packetsize', '') != '':
                args.append('-s')
                args.append(settings['packetsize'])
            if settings.get('disable_frag', '0') == '1':
                # FreeBSD -D (set DF) maps to iputils -M do (prohibit fragmentation)
                args.append('-M')
                args.append('do')
            if settings.get('interval', '') != '':
                args.append('-i')
                args.append(settings['interval'])
            args.append(settings.get('hostname', ''))
            if os.path.isfile(log_target):
                os.remove(log_target)
            # Replace FreeBSD daemon(8) with a detached background process whose
            # stdout/stderr are captured to the job log; record its pid so the
            # list/stop/remove actions can manage it.
            with open(log_target, 'w') as log_fh:
                proc = subprocess.Popen(
                    args,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True
                )
            with open(pid_target, 'w') as pid_fh:
                pid_fh.write(str(proc.pid))
    elif cmd_args.action == 'stop' and cmd_args.job in all_jobs:
        result['status'] = 'ok'
        result['stopped_processes'] = 0
        for pid in ping_pids(cmd_args.job):
            # SIGINT makes iputils ping write the final statistics block before
            # exiting (plain SIGTERM would terminate without a summary)
            subprocess.run(['kill', '-s', 'INT', pid])
            result['stopped_processes'] += 1
    elif cmd_args.action == 'remove' and cmd_args.job in all_jobs:
        result['status'] = 'ok'
        result['stopped_processes'] = 0
        for pid in ping_pids(cmd_args.job):
            subprocess.run(['kill', pid])
            result['stopped_processes'] += 1
        for filename in glob.glob("%s%s*" % (TEMP_DIR, cmd_args.job)):
            os.remove(filename)

    print (ujson.dumps(result))
