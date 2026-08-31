
"""
    Copyright (c) 2014-2023 Ad Schellevis <ad@opnsense.org>
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

    --------------------------------------------------------------------------------------
    package : configd
    function: session handling and authorisation
"""
import os
import struct
import socket
import pwd
import grp


class xucred:
    """ MurOS: FreeBSD passed the whole credential of the peer, user and up to
        sixteen groups, in one xucred structure read with LOCAL_PEERCRED. Linux
        has SO_PEERCRED, which carries the pid, the uid and the primary gid of
        the peer and nothing else, so the supplementary groups are resolved from
        the password database afterwards. The class keeps its name and its two
        accessors: it is what the actions check their allowed_groups against.

        Without this the credential stayed empty on Debian and every action
        carrying an allowed_groups constraint was refused, whoever called it.
    """
    def __init__(self, connection):
        self._user = None
        self._groups = set()
        self.cr_version = 0
        self.cr_pid = None
        self.cr_uid = None
        self.cr_ngroups = 0
        self.cr_groups = tuple()
        try:
            ucred_fmt = '3i'
            tmp = connection.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize(ucred_fmt)
            )
            self.cr_pid, self.cr_uid, cr_gid = struct.unpack(ucred_fmt, tmp)
        except (OSError, struct.error):
            return

        try:
            self._user = pwd.getpwuid(self.cr_uid).pw_name
        except KeyError:
            self._user = None

        gids = {cr_gid}
        if self._user is not None:
            gids.update(os.getgrouplist(self._user, cr_gid))

        for item in gids:
            try:
                self._groups.add(grp.getgrgid(item).gr_name)
            except KeyError:
                continue

        self.cr_groups = tuple(sorted(gids))
        self.cr_ngroups = len(self.cr_groups)

    def get_groups(self):
        return self._groups

    def get_user(self):
        return self._user


def get_session_context(connection):
    """
    :param instr: string with optional tags [field.$$]
    :return: xucred
    """

    return xucred(connection)
