#!/usr/bin/python3

"""
    Copyright (c) 2026 MurOS
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
     this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright notice,
     this list of conditions and the following disclaimer in the documentation
     and/or other materials provided with the distribution.

    THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES
    ARE DISCLAIMED. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DAMAGE.
    --------------------------------------------------------------------------------------
    report temperature sensors (Linux, read from /sys/class/thermal and hwmon)
"""
import glob
import json
import os
import re


def read_text(path):
    try:
        with open(path) as handle:
            return handle.read().strip()
    except OSError:
        return ''


def classify(label):
    name = (label or '').lower()
    if 'acpitz' in name:
        return 'zone', 'Zone'
    if 'k10temp' in name or 'zenpower' in name or 'amd' in name:
        return 'amd', 'AMD'
    if 'pch' in name:
        return 'platform', 'Platform'
    if 'coretemp' in name or 'x86_pkg' in name or 'core' in name or 'cpu' in name:
        return 'cpu', 'CPU'
    return 'other', 'Other'


def collect():
    sensors = []

    # ACPI / SoC thermal zones
    for zone in sorted(glob.glob('/sys/class/thermal/thermal_zone*')):
        raw = read_text(os.path.join(zone, 'temp'))
        if not raw:
            continue
        try:
            celsius = round(int(raw) / 1000.0, 1)
        except ValueError:
            continue
        ztype = read_text(os.path.join(zone, 'type')) or os.path.basename(zone)
        device = '%s (%s)' % (os.path.basename(zone), ztype)
        kind, kind_txt = classify(ztype)
        sensors.append((device, celsius, kind, kind_txt))

    # hwmon chips (coretemp, k10temp, nct67xx, ...)
    for chip in sorted(glob.glob('/sys/class/hwmon/hwmon*')):
        chip_name = read_text(os.path.join(chip, 'name')) or os.path.basename(chip)
        for inp in sorted(glob.glob(os.path.join(chip, 'temp*_input'))):
            raw = read_text(inp)
            if not raw:
                continue
            try:
                celsius = round(int(raw) / 1000.0, 1)
            except ValueError:
                continue
            label = read_text(inp.replace('_input', '_label'))
            device = chip_name if not label else '%s %s' % (chip_name, label)
            kind, kind_txt = classify('%s %s' % (chip_name, label))
            sensors.append((device, celsius, kind, kind_txt))

    result = []
    for device, celsius, kind, kind_txt in sensors:
        result.append({
            'device': device,
            'device_seq': int((re.findall(r'\d+', device) or ['0'])[0]),
            'temperature': '%s' % celsius,
            'type': kind,
            'type_translated': kind_txt,
        })
    return result


if __name__ == '__main__':
    try:
        print(json.dumps(collect()))
    except Exception:
        print(json.dumps([]))
