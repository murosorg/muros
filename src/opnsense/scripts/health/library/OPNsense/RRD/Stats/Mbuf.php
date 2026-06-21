<?php

/*
 * Copyright (C) 2024 Deciso B.V.
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 * INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 * AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 * OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

namespace OPNsense\RRD\Stats;

class Mbuf extends Base
{
    /**
     * MurOS: FreeBSD exposes network packet buffers as mbuf clusters via
     * `netstat -m`. The Linux equivalent of an mbuf is the socket buffer
     * (sk_buff), allocated from the skbuff slab caches. We read the active and
     * total object counts of those caches from /proc/slabinfo and keep the
     * existing datasets so the graph stays meaningful:
     *   current = sk_buffs currently in use
     *   total   = sk_buffs allocated (in use + free in the slab)
     *   cache   = allocated but free (total - current)
     *   max     = same as total; the kernel has no fixed mbuf ceiling here
     */
    public function run()
    {
        $active = 0;
        $num = 0;
        $found = false;

        $slabinfo = @file('/proc/slabinfo', FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
        if ($slabinfo === false) {
            return [];
        }

        /* Match every sk_buff slab cache; names vary across kernels
         * (skbuff_head_cache, skbuff_fclone_cache, skbuff_small_head, ...). */
        foreach ($slabinfo as $line) {
            $parts = preg_split('/\s+/', trim($line));
            if (count($parts) < 3 || strpos($parts[0], 'skbuff') !== 0) {
                continue;
            }
            $active += (int)$parts[1];
            $num += (int)$parts[2];
            $found = true;
        }

        if (!$found) {
            return [];
        }

        return [
            'current' => $active,
            'cache' => max(0, $num - $active),
            'total' => $num,
            'max' => $num,
        ];
    }
}
