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

class Memory extends Base
{
    public function run()
    {
        // FreeBSD exposed memory through vm.stats.vm.v_*_count sysctls; on Linux
        // the equivalent breakdown comes from /proc/meminfo (values in kB).
        $raw = @file_get_contents('/proc/meminfo');
        if ($raw === false) {
            return [];
        }

        $kb = [];
        foreach (explode("\n", $raw) as $line) {
            if (preg_match('/^(\w+):\s+(\d+)\s*kB/', $line, $m)) {
                $kb[$m[1]] = (float)$m[2];
            }
        }

        $total = $kb['MemTotal'] ?? 0;
        if ($total <= 0) {
            return [];
        }

        // map Linux memory accounting onto the dataset names of the existing
        // system-memory.rrd schema (active/inactive/free/cache/wire) so historical
        // graphs keep working. Linux has no "wired" class, approximate it with the
        // non-pageable kernel allocations.
        $cache = ($kb['Cached'] ?? 0) + ($kb['Buffers'] ?? 0);
        $wire = ($kb['Slab'] ?? 0) + ($kb['KernelStack'] ?? 0) + ($kb['PageTables'] ?? 0);

        return [
            'active' => (($kb['Active'] ?? 0) / $total) * 100.0,
            'inactive' => (($kb['Inactive'] ?? 0) / $total) * 100.0,
            'free' => (($kb['MemFree'] ?? 0) / $total) * 100.0,
            'cache' => ($cache / $total) * 100.0,
            'wire' => ($wire / $total) * 100.0,
        ];
    }
}
