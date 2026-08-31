<?php

/*
 * Copyright (C) 2014-2015 Deciso B.V.
 * Copyright (C) 2004 Scott Ullrich <sullrich@gmail.com>
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

require_once("guiconfig.inc");
require_once("interfaces.inc");

/*
 * MurOS: the page used to run "ifconfig <if> scan" and parse "list scan" and
 * "list sta". Neither exists on Debian and the web server runs unprivileged,
 * so the radio is questioned through configd, which returns the output of iw
 * as JSON. The columns follow what nl80211 reports: a Linux station has no
 * association id and no transmit sequence numbers, it has signal strength,
 * negotiated rates and traffic counters.
 */
$scan = [];
$peers = [];

if ($_SERVER['REQUEST_METHOD'] === 'GET') {
    if (!empty($_GET['if'])) {
        $if = $_GET['if'];
    } else {
        /* if no interface is provided this invoke is invalid */
        header(url_safe('Location: /index.php'));
        exit;
    }
    $rwlif = get_real_interface($if);
    if (!empty($_GET['rescanwifi'])) {
        configd_run(sprintf('interface wireless scan %s', $rwlif));
        header(url_safe('Location: /status_wireless.php?if=%s', [$if]));
        exit;
    }
    $scan = json_decode(configd_run(sprintf('interface wireless scan %s', $rwlif)), true) ?: [];
    $peers = json_decode(configd_run(sprintf('interface wireless stations %s', $rwlif)), true) ?: [];
}

function wireless_bytes($bytes)
{
    $units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    $index = 0;
    while ($bytes >= 1024 && $index < count($units) - 1) {
        $bytes /= 1024;
        $index++;
    }

    return sprintf('%.1f %s', $bytes, $units[$index]);
}

function wireless_age($seconds)
{
    if ($seconds < 60) {
        return sprintf(gettext('%d s'), $seconds);
    }

    return sprintf(gettext('%d min'), (int)($seconds / 60));
}

include("head.inc");

?>
<body>
<?php include("fbegin.inc"); ?>
  <section class="page-content-main">
    <div class="container-fluid">
      <?php if (isset($savemsg)) print_info_box($savemsg); ?>
      <div class="row">
        <section class="col-xs-12">
          <form method="post" name="iform" id="iform">
            <div class="content-box table-responsive __mb">
            <input type="hidden" name="if" id="if" value="<?= html_safe($if) ?>">
            <header class="content-box-head container-fluid">
              <h3>
                <?= gettext('Nearby access points or ad-hoc peers') ?>
                <a href="<?= 'status_wireless.php?if=' . html_safe($if) . '&rescanwifi=1' ?>" class="btn btn-xs btn-primary pull-right"><i class="fa fa-plus-circle fa-fw"></i> <?= gettext('Rescan') ?></a>
              </h3>
            </header>
              <table class="table table-striped">
                <thead>
                  <tr>
                    <th><?= gettext('SSID') ?></th>
                    <th><?= gettext('BSSID') ?></th>
                    <th><?= gettext('CHAN') ?></th>
                    <th><?= gettext('FREQ') ?></th>
                    <th><?= gettext('RATE') ?></th>
                    <th><?= gettext('SIGNAL') ?></th>
                    <th><?= gettext('INT') ?></th>
                    <th><?= gettext('MODE') ?></th>
                    <th><?= gettext('SECURITY') ?></th>
                  </tr>
                </thead>
                <tbody>
<?php foreach ($scan as $cell): ?>
                  <tr>
                    <td><?= html_safe($cell['ssid'] === '' ? gettext('hidden') : $cell['ssid']) ?></td>
                    <td><?= html_safe($cell['bssid']) ?></td>
                    <td><?= html_safe($cell['channel']) ?></td>
                    <td><?= html_safe(sprintf('%d MHz', $cell['freq'])) ?></td>
                    <td><?= html_safe(sprintf('%g Mbit/s', $cell['rate'])) ?></td>
                    <td><?= html_safe($cell['rssi'] === null ? '' : sprintf('%.0f dBm', $cell['rssi'])) ?></td>
                    <td><?= html_safe($cell['interval']) ?></td>
                    <td><?= html_safe($cell['mode']) ?></td>
                    <td><?= html_safe($cell['security']) ?></td>
                  </tr>
<?php endforeach ?>
<?php if (empty($scan)): ?>
                  <tr>
                    <td colspan="9"><?= gettext('No networks found. A radio serving an access point cannot scan, and a station reports what it last heard.') ?></td>
                  </tr>
<?php endif ?>
                </tbody>
              </table>
            </div>
            <div class="content-box table-responsive">
              <header class="content-box-head container-fluid">
                <h3><?= gettext('Associated or ad-hoc peers') ?></h3>
              </header>
              <table class="table table-striped">
                <thead>
                  <tr>
                    <th><?= gettext('ADDR') ?></th>
                    <th><?= gettext('SIGNAL') ?></th>
                    <th><?= gettext('TX RATE') ?></th>
                    <th><?= gettext('RX RATE') ?></th>
                    <th><?= gettext('TX') ?></th>
                    <th><?= gettext('RX') ?></th>
                    <th><?= gettext('IDLE') ?></th>
                    <th><?= gettext('UPTIME') ?></th>
                    <th><?= gettext('FLAGS') ?></th>
                  </tr>
                </thead>
                <tbody>
<?php foreach ($peers as $peer): ?>
                  <tr>
                    <td><?= html_safe($peer['mac']) ?></td>
                    <td><?= html_safe($peer['signal'] === null ? '' : sprintf('%.0f dBm', $peer['signal'])) ?></td>
                    <td><?= html_safe($peer['tx_rate']) ?></td>
                    <td><?= html_safe($peer['rx_rate']) ?></td>
                    <td><?= html_safe(wireless_bytes($peer['tx_bytes'])) ?></td>
                    <td><?= html_safe(wireless_bytes($peer['rx_bytes'])) ?></td>
                    <td><?= html_safe(wireless_age((int)($peer['inactive'] / 1000))) ?></td>
                    <td><?= html_safe(wireless_age($peer['connected'])) ?></td>
                    <td><?= html_safe(implode(', ', $peer['flags'])) ?></td>
                  </tr>
<?php endforeach ?>
<?php if (empty($peers)): ?>
                  <tr>
                    <td colspan="9"><?= gettext('No peer is associated with this radio.') ?></td>
                  </tr>
<?php endif ?>
                </tbody>
                <tfoot>
                  <tr>
                    <td colspan="9">
                      <b><?= gettext('Flags:') ?></b> <?= gettext('authorized = the peer may send data, authenticated = the peer completed the handshake, wmm/wme = quality of service is negotiated, preamble = short preamble in use') ?>
                    </td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </form>
        </section>
      </div>
    </div>
  </section>
<?php include("foot.inc") ?>
