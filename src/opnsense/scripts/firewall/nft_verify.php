#!/usr/bin/env php
<?php

/*
 * MurOS: does the configuration the GUI shows actually reach the kernel?
 *
 * Every silent failure found in this port so far had the same shape: an item
 * was accepted by the GUI, stored in the configuration, listed back to the
 * operator, and never turned into a rule. Comparing the generated ruleset with
 * the loaded one cannot catch that, since both agree on the mistake. So this
 * walks the configuration instead, and for every item that is supposed to
 * produce a rule it looks for the tag that rule would carry.
 *
 * Four answers are possible for an item:
 *   applied   the ruleset in the kernel carries its tag
 *   scheduled the generator left it out on purpose, it is outside the time
 *             window of its schedule
 *   pending   the ruleset the current configuration produces carries its tag,
 *             the loaded one does not, so the filter has not been reloaded
 *             since the configuration changed
 *   missing   neither carries it: nothing the box does will ever apply it
 *
 * A rule loaded for an item that no longer exists is reported as stale, and a
 * rule loaded without one of the options it carries, because that option has
 * no counterpart on this platform, is reported as degraded: it is in the
 * kernel, it does not do everything it says.
 *
 * Usage: nft_verify.php [--json] [--ruleset <file>] [config.xml]
 * Exit status is 1 when an item is missing or pending, 0 otherwise.
 */

$json = false;
$rulesetFile = null;
$args = [];
$argc = count($argv);
for ($i = 1; $i < $argc; $i++) {
    if ($argv[$i] === '--json') {
        $json = true;
    } elseif ($argv[$i] === '--ruleset' && isset($argv[$i + 1])) {
        /* check a ruleset kept in a file rather than the loaded one */
        $rulesetFile = $argv[++$i];
    } else {
        $args[] = $argv[$i];
    }
}

$path = $args[0] ?? '/conf/config.xml';
if (!is_file($path)) {
    fwrite(STDERR, "no such configuration: $path\n");
    exit(2);
}

$cfg = @simplexml_load_file($path);
if ($cfg === false) {
    fwrite(STDERR, "cannot parse the configuration: $path\n");
    exit(2);
}

/* The uuid an item carries, whichever way it stores it. */
function item_uuid(SimpleXMLElement $item): string
{
    $uuid = trim((string)($item['uuid'] ?? ''));
    if ($uuid === '') {
        $uuid = trim((string)($item->uuid ?? ''));
    }

    return preg_replace('/[^A-Za-z0-9-]/', '', $uuid);
}

/* Legacy items are disabled by the presence of an element, model items by its
 * value, and both are enabled by default when the element is absent. */
function legacy_enabled(SimpleXMLElement $item): bool
{
    return !isset($item->disabled) || trim((string)$item->disabled) === '0';
}

function model_enabled(SimpleXMLElement $item): bool
{
    return trim((string)($item->enabled ?? '1')) !== '0';
}

function describe(SimpleXMLElement $item): string
{
    foreach (['descr', 'description'] as $field) {
        $value = trim((string)($item->{$field} ?? ''));
        if ($value !== '') {
            return $value;
        }
    }

    return '';
}

/* Everything in the configuration that is supposed to become a rule. */
function expected_items(SimpleXMLElement $cfg): array
{
    $items = [];

    $legacy = [
        'filter rule' => $cfg->filter->rule ?? null,
        'port forward' => $cfg->nat->rule ?? null,
        'outbound nat' => $cfg->nat->outbound->rule ?? null,
        'one-to-one nat' => $cfg->nat->onetoone ?? null,
    ];
    foreach ($legacy as $kind => $nodes) {
        if ($nodes === null) {
            continue;
        }
        foreach ($nodes as $item) {
            if (!legacy_enabled($item)) {
                continue;
            }
            $uuid = item_uuid($item);
            if ($uuid === '') {
                /* an item with no uuid cannot be followed into the ruleset */
                continue;
            }
            $items[] = ['kind' => $kind, 'uuid' => $uuid, 'description' => describe($item)];
        }
    }

    $root = $cfg->OPNsense->Firewall->Filter ?? null;
    if ($root !== null) {
        $model = [
            'filter rule' => $root->rules->rule ?? null,
            'source nat' => $root->snatrules->rule ?? null,
            'one-to-one nat' => $root->onetoone->rule ?? null,
            'npt' => $root->npt->rule ?? null,
        ];
        foreach ($model as $kind => $nodes) {
            if ($nodes === null) {
                continue;
            }
            foreach ($nodes as $item) {
                if (!model_enabled($item)) {
                    continue;
                }
                $uuid = item_uuid($item);
                if ($uuid === '') {
                    continue;
                }
                $items[] = ['kind' => $kind, 'uuid' => $uuid, 'description' => describe($item)];
            }
        }
    }

    return $items;
}

/* The tags carried by a ruleset, whether it comes from the kernel or from a
 * freshly generated script. */
function tags_of_ruleset(string $text): array
{
    $tags = [];
    if (preg_match_all('/comment "([^"]*)"/', $text, $matches)) {
        foreach ($matches[1] as $comment) {
            $tag = explode(' ', trim($comment))[0];
            if ($tag !== '') {
                $tags[$tag] = true;
            }
        }
    }

    return $tags;
}

function running_ruleset(?string $file): ?string
{
    if ($file !== null) {
        $text = @file_get_contents($file);

        return $text === false ? null : $text;
    }

    $output = [];
    $status = 0;
    exec('/usr/sbin/nft list ruleset 2>/dev/null', $output, $status);
    if ($status !== 0) {
        return null;
    }

    return implode("\n", $output);
}

function intended_ruleset(string $path, array &$skipped, array &$degraded): ?string
{
    $output = [];
    $status = 0;
    $reportFile = tempnam(sys_get_temp_dir(), 'nftverify');
    exec(
        sprintf(
            '%s/nft_build.php --report %s %s 2>/dev/null',
            escapeshellarg(__DIR__),
            escapeshellarg($reportFile),
            escapeshellarg($path)
        ),
        $output,
        $status
    );

    /* what the generator left out on purpose, so a rule outside the window of
       its schedule is not reported as one that will never be applied */
    $report = json_decode((string)@file_get_contents($reportFile), true);
    @unlink($reportFile);
    foreach ($report['skipped'] ?? [] as $entry) {
        if (!empty($entry['uuid'])) {
            $skipped[$entry['uuid']] = $entry;
        }
    }
    foreach ($report['degraded'] ?? [] as $entry) {
        if (!empty($entry['uuid'])) {
            $degraded[$entry['uuid']][] = trim($entry['option'] . ' (' . $entry['detail'] . ')');
        }
    }

    if ($status !== 0) {
        return null;
    }

    return implode("\n", $output);
}

$items = expected_items($cfg);
$running = running_ruleset($rulesetFile);
$skipped = [];
$degraded = [];
$intended = intended_ruleset($path, $skipped, $degraded);

if ($intended === null) {
    fwrite(STDERR, "the ruleset generator failed, nothing can be verified\n");
    exit(2);
}

$intendedTags = tags_of_ruleset($intended);
$runningTags = $running === null ? [] : tags_of_ruleset($running);

$report = ['applied' => [], 'scheduled' => [], 'pending' => [], 'missing' => [], 'stale' => [], 'degraded' => []];
$known = [];
foreach ($items as $item) {
    $known[$item['uuid']] = true;
    if (isset($degraded[$item['uuid']])) {
        $option = $item;
        $option['description'] = trim($item['description'] . ' [' . implode(', ', $degraded[$item['uuid']]) . ']');
        $report['degraded'][] = $option;
    }
    if (isset($runningTags[$item['uuid']])) {
        $report['applied'][] = $item;
    } elseif (isset($skipped[$item['uuid']])) {
        $item['description'] = trim(sprintf(
            '%s (outside the window of schedule %s)',
            $item['description'],
            $skipped[$item['uuid']]['detail']
        ));
        $report['scheduled'][] = $item;
    } elseif (isset($intendedTags[$item['uuid']])) {
        $report['pending'][] = $item;
    } else {
        $report['missing'][] = $item;
    }
}

/* A tag loaded in the kernel that belongs to no enabled item: either the item
 * was removed or disabled and the filter was not reloaded. Only tags shaped
 * like a uuid are considered, the automatic rules carry plain words. */
foreach (array_keys($runningTags) as $tag) {
    if (isset($known[$tag])) {
        continue;
    }
    if (strlen($tag) === 36 && substr_count($tag, '-') === 4) {
        $report['stale'][] = ['kind' => 'rule', 'uuid' => $tag, 'description' => ''];
    }
}

$failed = count($report['missing']) + count($report['pending']);

if ($json) {
    echo json_encode([
        'ruleset_loaded' => $running !== null,
        'counts' => [
            'applied' => count($report['applied']),
            'scheduled' => count($report['scheduled']),
            'pending' => count($report['pending']),
            'missing' => count($report['missing']),
            'stale' => count($report['stale']),
            'degraded' => count($report['degraded']),
        ],
        'pending' => $report['pending'],
        'missing' => $report['missing'],
        'stale' => $report['stale'],
        'degraded' => $report['degraded'],
    ]) . "\n";
    exit($failed > 0 ? 1 : 0);
}

function print_group(string $title, array $rows)
{
    if (empty($rows)) {
        return;
    }
    echo $title . "\n";
    foreach ($rows as $row) {
        printf("  %-16s %s  %s\n", $row['kind'], $row['uuid'], $row['description']);
    }
    echo "\n";
}

if ($running === null) {
    echo "the running ruleset could not be read, only the generated one was checked\n\n";
}

printf(
    "%d configured items, %d applied, %d outside their schedule, %d pending a reload, "
        . "%d never applied, %d stale in the kernel, %d with an ignored option\n\n",
    count($items),
    count($report['applied']),
    count($report['scheduled']),
    count($report['pending']),
    count($report['missing']),
    count($report['stale']),
    count($report['degraded'])
);

print_group('never applied, the generator produces no rule for these:', $report['missing']);
print_group('outside their schedule, left out on purpose until the window opens:', $report['scheduled']);
print_group('pending, the filter has not been reloaded since these changed:', $report['pending']);
print_group('stale, loaded in the kernel but no longer configured:', $report['stale']);
print_group('loaded, but an option they carry has no counterpart here:', $report['degraded']);

if ($failed === 0 && count($report['stale']) === 0 && count($report['degraded']) === 0) {
    echo "the configuration and the ruleset agree\n";
}

exit($failed > 0 ? 1 : 0);
