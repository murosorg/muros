# apt.muros.org root page

Version-controlled copy of the human-facing files served at the root of
`apt.muros.org`:

- `index.html` - titled landing page (full SEO: title, meta description,
  canonical, OpenGraph/Twitter, JSON-LD, favicon), dark-mode aware, styled to
  match muros.org. Self-contained: the only external assets are the logo,
  favicon and OG image pulled from muros.org.
- `robots.txt` - keeps crawlers out of `dists/` and `pool/`.

## Deployment

The live files sit in `/opt/muros/apt/` on the apt host, served by nginx
(`/etc/nginx/sites-available/apt.muros.conf`, `root /opt/muros/apt`,
`index index.html`). The reprepro repository (`dists/`, `pool/`, `conf/`,
`db/`) lives in the same directory and is untouched by these files.

To update the landing page:

    scp packaging/apt/index.html packaging/apt/robots.txt root@apt.muros.org:/opt/muros/apt/

This repo is the source of truth; edit here and push to the host.
