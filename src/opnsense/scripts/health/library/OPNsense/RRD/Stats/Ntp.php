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

class Ntp extends Base
{
    public function run()
    {
        if (!self::$metadata['ntp_statsgraph']) {
            return;
        }

        $data = [];

        /*
         * MurOS: the time daemon is chrony, so the numbers come from the single
         * CSV line of "chronyc -c tracking" instead of the ntpq variables.
         * Offsets and dispersion are reported in seconds and converted to the
         * milliseconds the graph expects, frequencies stay in ppm. The clock
         * jitter of the reference implementation has no counterpart and is left
         * out, the update marks it unknown.
         */
        $tracking = $this->shellCmd('/usr/bin/chronyc -c tracking');
        $fields = explode(',', trim((string)($tracking[0] ?? '')));

        if (count($fields) < 12) {
            return $data;
        }

        $data['offset'] = (float)$fields[5] * 1000;
        $data['sjit'] = (float)$fields[6] * 1000;
        $data['freq'] = (float)$fields[7];
        $data['wander'] = (float)$fields[9];
        $data['disp'] = (float)$fields[11] * 1000;

        return $data;
    }
}
