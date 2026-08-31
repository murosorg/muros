#!/usr/bin/python3

"""
    Copyright (c) 2026 Ecritel
    All rights reserved.

    MurOS NetFlow collector.

    Local collection used to be flowd, which is not packaged for Debian. Only
    the daemon was missing though: the reporting side reads its log with the
    parser in lib/flowparser.py, so this collector receives the datagrams the
    exporters send to 127.0.0.1:2056 and appends records in that same on disk
    format. NetFlow version 5 and version 9 are decoded, which is what the
    exporter of this platform produces.

    Usage: collector.py [listen address] [port]
"""

import os
import signal
import socket
import struct
import sys
import syslog
import time

LOGFILE = '/var/log/flowd.log'
PIDFILE = '/var/run/flowd.pid'
DEFAULT_ADDRESS = '127.0.0.1'
DEFAULT_PORT = 2056

# bit positions of the fields, in the order the parser expects them on disk
FIELDS = [
    'tag', 'recv_time', 'proto_flags_tos', 'agent_addr4', 'agent_addr6',
    'src_addr4', 'src_addr6', 'dst_addr4', 'dst_addr6', 'gateway_addr4',
    'gateway_addr6', 'srcdst_port', 'packets', 'octets', 'if_indices',
    'agent_info', 'flow_times', 'as_info', 'flow_engine_info'
]

# the subset written here, a record always carries all of them
WRITTEN = [
    'recv_time', 'proto_flags_tos', 'agent_addr4', 'src_addr4', 'dst_addr4',
    'gateway_addr4', 'srcdst_port', 'packets', 'octets', 'if_indices',
    'agent_info', 'flow_times'
]


class FlowdWriter:
    """ append only writer for the flowd log, reopening the file when the
        aggregator has rotated it away from under us
    """

    def __init__(self, filename):
        self._filename = filename
        self._handle = None
        self._inode = None

    def _open(self):
        self._handle = open(self._filename, 'ab', buffering=0)
        self._inode = os.fstat(self._handle.fileno()).st_ino

    def close(self):
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def write(self, payload):
        try:
            rotated = not os.path.exists(self._filename) or \
                os.stat(self._filename).st_ino != self._inode
        except OSError:
            rotated = True

        if self._handle is None or rotated:
            if self._handle is not None:
                self._handle.close()
            self._open()

        self._handle.write(payload)


def flowd_record(flow):
    """ serialise a decoded flow into a flowd record
    """
    mask = 0
    for index, name in enumerate(FIELDS):
        if name in WRITTEN:
            mask |= pow(2, index)

    payload = b''
    payload += struct.pack('>II', flow['recv_sec'], flow['recv_usec'])
    payload += struct.pack('BBBB', flow['tcp_flags'], flow['protocol'], flow['tos'], 0)
    payload += flow['agent_addr']
    payload += flow['src_addr']
    payload += flow['dst_addr']
    payload += flow['gateway_addr']
    payload += struct.pack('>HH', flow['src_port'], flow['dst_port'])
    payload += struct.pack('>Q', flow['packets'])
    payload += struct.pack('>Q', flow['octets'])
    payload += struct.pack('>II', flow['if_ndx_in'], flow['if_ndx_out'])
    # the parser reads the uptime from the first word and the version from the
    # fourth field of the agent information
    payload += struct.pack('>IIIHH', flow['sys_uptime_ms'], 0, 0, flow['version'], 0)
    payload += struct.pack('>II', flow['flow_start_ms'], flow['flow_finish_ms'])

    header = struct.pack('BBHI', 2, len(payload) // 4, 0, socket.htonl(mask))

    return header + payload


def decode_v5(data, agent, recv_sec, recv_usec):
    """ version 5 is a fixed layout, 24 byte header plus 48 byte records
    """
    if len(data) < 24:
        return

    count, uptime = struct.unpack('>HI', data[2:8])

    for index in range(count):
        offset = 24 + index * 48
        if offset + 48 > len(data):
            break
        record = data[offset:offset + 48]
        src, dst, nexthop = record[0:4], record[4:8], record[8:12]
        if_in, if_out = struct.unpack('>HH', record[12:16])
        packets, octets, first, last = struct.unpack('>IIII', record[16:32])
        src_port, dst_port = struct.unpack('>HH', record[32:36])
        tcp_flags, protocol, tos = struct.unpack('BBB', record[37:40])

        yield {
            'version': 5,
            'recv_sec': recv_sec,
            'recv_usec': recv_usec,
            'agent_addr': agent,
            'src_addr': src,
            'dst_addr': dst,
            'gateway_addr': nexthop,
            'src_port': src_port,
            'dst_port': dst_port,
            'protocol': protocol,
            'tcp_flags': tcp_flags,
            'tos': tos,
            'packets': packets,
            'octets': octets,
            'if_ndx_in': if_in,
            'if_ndx_out': if_out,
            'sys_uptime_ms': uptime,
            'flow_start_ms': first,
            'flow_finish_ms': last,
        }


# version 9 field types that are needed, everything else is skipped
V9_FIELDS = {
    1: 'octets', 2: 'packets', 4: 'protocol', 5: 'tos', 6: 'tcp_flags',
    7: 'src_port', 8: 'src_addr', 10: 'if_ndx_in', 11: 'dst_port',
    12: 'dst_addr', 14: 'if_ndx_out', 15: 'gateway_addr', 21: 'flow_finish_ms',
    22: 'flow_start_ms', 27: 'src_addr', 28: 'dst_addr', 62: 'gateway_addr'
}
V9_ADDRESSES = ['src_addr', 'dst_addr', 'gateway_addr']


def decode_v9(data, agent, recv_sec, recv_usec, templates):
    """ version 9 carries templates, so the layout of a data flowset is only
        known once its template has been received on the same session
    """
    if len(data) < 20:
        return

    count, uptime, _, _, source_id = struct.unpack('>HIIII', data[2:20])
    offset = 20

    while offset + 4 <= len(data):
        flowset_id, length = struct.unpack('>HH', data[offset:offset + 4])
        if length < 4 or offset + length > len(data):
            break
        body = data[offset + 4:offset + length]
        offset += length

        if flowset_id == 0:
            # template flowset
            position = 0
            while position + 4 <= len(body):
                template_id, field_count = struct.unpack('>HH', body[position:position + 4])
                position += 4
                fields = []
                for _ in range(field_count):
                    if position + 4 > len(body):
                        break
                    field_type, field_length = struct.unpack('>HH', body[position:position + 4])
                    position += 4
                    fields.append((field_type, field_length))
                templates[(agent, source_id, template_id)] = fields
            continue

        if flowset_id < 256:
            # options template or reserved, nothing to collect
            continue

        fields = templates.get((agent, source_id, flowset_id))
        if fields is None:
            continue

        size = sum([length for _, length in fields])
        if size == 0:
            continue

        for index in range(len(body) // size):
            record = body[index * size:(index + 1) * size]
            values = {}
            position = 0
            for field_type, field_length in fields:
                raw = record[position:position + field_length]
                position += field_length
                name = V9_FIELDS.get(field_type)
                if name is None:
                    continue
                if name in V9_ADDRESSES:
                    values[name] = raw
                else:
                    values[name] = int.from_bytes(raw, 'big')

            if 'src_addr' not in values or 'dst_addr' not in values:
                continue

            yield {
                'version': 9,
                'recv_sec': recv_sec,
                'recv_usec': recv_usec,
                'agent_addr': agent,
                'src_addr': values['src_addr'],
                'dst_addr': values['dst_addr'],
                'gateway_addr': values.get('gateway_addr', bytes(4)),
                'src_port': values.get('src_port', 0),
                'dst_port': values.get('dst_port', 0),
                'protocol': values.get('protocol', 0),
                'tcp_flags': values.get('tcp_flags', 0),
                'tos': values.get('tos', 0),
                'packets': values.get('packets', 0),
                'octets': values.get('octets', 0),
                'if_ndx_in': values.get('if_ndx_in', 0),
                'if_ndx_out': values.get('if_ndx_out', 0),
                'sys_uptime_ms': uptime,
                'flow_start_ms': values.get('flow_start_ms', uptime),
                'flow_finish_ms': values.get('flow_finish_ms', uptime),
            }


def main():
    address = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ADDRESS
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT

    syslog.openlog('netflow', logoption=syslog.LOG_PID, facility=syslog.LOG_LOCAL4)

    handle = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    handle.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    handle.bind((address, port))

    writer = FlowdWriter(LOGFILE)
    templates = {}

    # the aggregator rotates the log and signals the collector to reopen it, the
    # same contract flowd offered through the very same pid file
    with open(PIDFILE, 'w') as handle_pid:
        handle_pid.write('%d' % os.getpid())

    signal.signal(signal.SIGUSR1, lambda signum, frame: writer.close())
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    syslog.syslog(syslog.LOG_NOTICE, 'collecting flows on %s:%d' % (address, port))

    while True:
        data, sender = handle.recvfrom(65535)
        if len(data) < 4:
            continue

        agent = socket.inet_aton(sender[0])
        received = time.time()
        recv_sec = int(received)
        recv_usec = int((received - recv_sec) * 1000000)
        version = struct.unpack('>H', data[0:2])[0]

        if version == 5:
            flows = decode_v5(data, agent, recv_sec, recv_usec)
        elif version == 9:
            flows = decode_v9(data, agent, recv_sec, recv_usec, templates)
        else:
            continue

        for flow in flows:
            try:
                writer.write(flowd_record(flow))
            except (OSError, struct.error) as error:
                syslog.syslog(syslog.LOG_ERR, 'could not store a flow (%s)' % error)


if __name__ == '__main__':
    main()
