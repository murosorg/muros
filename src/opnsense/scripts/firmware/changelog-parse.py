#!/usr/bin/python3

"""
    Copyright (c) 2026 MurOS
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
     this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in the
     documentation and/or other materials provided with the distribution.

    THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES.
    --------------------------------------------------------------------------------------
    turn a Debian changelog into the per version set the firmware page reads

    OPNsense downloaded a signed tarball of pre-rendered changelogs. There is
    no such feed here and there does not need to be one: a Debian package
    carries its own changelog, so the history of every version is already on
    the box, and apt can fetch the one of a version not installed yet.

    Reads one or more concatenated changelogs and writes, per version, a text
    and an HTML rendering next to an index the GUI lists.
"""
import html
import json
import os
import re
import sys
from email.utils import parsedate_to_datetime

HEADER = re.compile(r'^(?P<source>[a-z0-9][a-z0-9+.-]*) \((?P<version>[^()\s]+)\)\s+(?P<dists>[^;]+);')
TRAILER = re.compile(r'^ -- .*  (?P<date>.+)$')


def parse(text):
    """ split a changelog into entries, newest first as the format demands """
    entries = []
    current = None

    for line in text.splitlines():
        found = HEADER.match(line)
        if found:
            current = {'version': found.group('version'), 'date': '', 'lines': []}
            entries.append(current)
            continue
        if current is None:
            continue
        found = TRAILER.match(line)
        if found:
            try:
                current['date'] = parsedate_to_datetime(found.group('date').strip()).strftime('%Y-%m-%d')
            except (TypeError, ValueError):
                current['date'] = ''
            current = None
            continue
        current['lines'].append(line.rstrip())

    return entries


def render_text(entry):
    """ the entry body, without the blank lines the format pads it with """
    body = '\n'.join(entry['lines']).strip('\n')

    return body + '\n' if body else ''


def render_html(entry):
    """ the same body as a list, each bullet of the changelog an item """
    items = []
    for line in entry['lines']:
        stripped = line.strip()
        if stripped.startswith('* ') or stripped.startswith('+ ') or stripped.startswith('- '):
            items.append(stripped[2:].strip())
        elif stripped and items:
            items[-1] = '%s %s' % (items[-1], stripped)

    if not items:
        return '<pre>%s</pre>\n' % html.escape(render_text(entry))

    out = ['<h2>%s</h2>' % html.escape(entry['version']), '<ul>']
    for item in items:
        out.append('  <li>%s</li>' % html.escape(item))
    out.append('</ul>')

    return '\n'.join(out) + '\n'


def write(destdir, entries):
    """ one text and one HTML file per version, and the index listing them """
    os.makedirs(destdir, exist_ok=True)

    index = []
    seen = set()
    for entry in entries:
        version = entry['version']
        if version in seen:
            continue
        seen.add(version)

        body = render_text(entry)
        if body == '':
            continue

        with open(os.path.join(destdir, '%s.txt' % version), 'w') as fhandle:
            fhandle.write(body)
        with open(os.path.join(destdir, '%s.htm' % version), 'w') as fhandle:
            fhandle.write(render_html(entry))

        series = '.'.join(version.split('.')[:2])
        index.append({'version': version, 'date': entry['date'], 'series': series})

    with open(os.path.join(destdir, 'index.json'), 'w') as fhandle:
        json.dump(index, fhandle)

    return len(index)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('usage: changelog-parse.py <changelog> <destination>', file=sys.stderr)
        sys.exit(1)

    try:
        with open(sys.argv[1], errors='replace') as source:
            content = source.read()
    except OSError:
        content = ''

    write(sys.argv[2], parse(content))
