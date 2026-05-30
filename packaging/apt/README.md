# apt.muros.org root page

`index.html` is the human-facing landing page served at the root of
`apt.muros.org`. It is self-contained (inline CSS, no external assets except
the favicon and OG image pulled from muros.org) so it can sit on the apt host
without depending on the marketing site build.

Deploy: have `muros-apt-publish` copy this file to the repository web root
(next to `install.sh`, `muros.asc`, `dists/`, `pool/`). It replaces the bare
directory autoindex with a proper titled page while apt itself keeps using
`dists/` and `pool/` as usual.
