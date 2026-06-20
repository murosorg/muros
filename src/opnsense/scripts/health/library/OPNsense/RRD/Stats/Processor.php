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

class Processor extends Base
{
    public function run()
    {
        // FreeBSD sampled cpu time through the cpustats helper; on Linux we derive
        // the same user/nice/system/interrupt split from two /proc/stat reads.
        $first = $this->readCpu();
        usleep(250000);
        $second = $this->readCpu();
        if ($first === null || $second === null) {
            return [];
        }

        $delta = [];
        $total = 0.0;
        foreach ($second as $key => $value) {
            $delta[$key] = $value - ($first[$key] ?? 0);
            $total += $delta[$key];
        }
        if ($total <= 0) {
            return [];
        }

        // task (thread) count, matching the threaded "ps uxaH" count of the original;
        // /proc/loadavg reports it as the denominator of its running/total field.
        $processes = 0;
        $load = @file_get_contents('/proc/loadavg');
        if ($load !== false && preg_match('#\d+/(\d+)#', $load, $m)) {
            $processes = (int)$m[1];
        }

        return [
            'user' => $delta['user'] / $total * 100.0,
            'nice' => $delta['nice'] / $total * 100.0,
            'system' => $delta['system'] / $total * 100.0,
            'interrupt' => ($delta['irq'] + $delta['softirq']) / $total * 100.0,
            'processes' => $processes,
        ];
    }

    /**
     * read the aggregate cpu time counters from /proc/stat
     * @return array|null jiffies per state, or null when unavailable
     */
    private function readCpu()
    {
        $raw = @file_get_contents('/proc/stat');
        if ($raw === false) {
            return null;
        }
        foreach (explode("\n", $raw) as $line) {
            if (strpos($line, 'cpu ') === 0) {
                // cpu user nice system idle iowait irq softirq steal guest guest_nice
                $p = preg_split('/\s+/', trim($line));
                return [
                    'user' => (float)($p[1] ?? 0),
                    'nice' => (float)($p[2] ?? 0),
                    'system' => (float)($p[3] ?? 0),
                    'idle' => (float)($p[4] ?? 0),
                    'iowait' => (float)($p[5] ?? 0),
                    'irq' => (float)($p[6] ?? 0),
                    'softirq' => (float)($p[7] ?? 0),
                    'steal' => (float)($p[8] ?? 0),
                ];
            }
        }
        return null;
    }
}
