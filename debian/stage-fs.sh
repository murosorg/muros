#!/bin/sh
# Stage the MurOS source tree into a Debian package root.
# Mirrors the OPNsense src/Makefile tree mapping (Mk/core.mk install loop),
# adapted for Debian: drops FreeBSD-only trees and relocates systemd units.
set -eu

DEST="$1"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOTDIR="$(cd "$HERE/.." && pwd)"
SRC="$ROOTDIR/src"
TOKENS="$HERE/tokens.sed"

# tree:install_root pairs (FreeBSD src/root tree is intentionally skipped)
stage_tree() {
    tree="$1"; root="$2"
    [ -d "$SRC/$tree" ] || return 0
    mkdir -p "$DEST$root/$tree"
    ( cd "$SRC/$tree" && tar -cf - . ) | ( cd "$DEST$root/$tree" && tar -xf - )
}

stage_tree bin       /usr/local
stage_tree etc       /usr/local
stage_tree libexec   /usr/local
stage_tree opnsense  /usr/local
stage_tree sbin      /usr/local
stage_tree www       /usr/local
stage_tree man       /usr/local/share

# Resolve .in (token substitution), .link (symlinks); keep .sample verbatim.
find "$DEST" -type f -name '*.in' | while read -r f; do
    out="${f%.in}"
    sed -f "$TOKENS" "$f" > "$out"
    rm -f "$f"
done
find "$DEST" -type f -name '*.link' | while read -r f; do
    target="$(cat "$f")"
    case "$f" in *python3.link) target=/usr/bin/python3 ;; esac
    ln -sfn "$target" "${f%.link}"
    rm -f "$f"
done

# Relocate systemd units from the OPNsense /usr/local/etc location to the
# Debian package unit directory so systemd and dh_installsystemd find them.
if [ -d "$DEST/usr/local/etc/systemd/system" ]; then
    mkdir -p "$DEST/lib/systemd/system"
    for u in "$DEST/usr/local/etc/systemd/system"/*.service; do
        [ -e "$u" ] || continue
        mv "$u" "$DEST/lib/systemd/system/"
    done
    rmdir "$DEST/usr/local/etc/systemd/system" 2>/dev/null || true
fi

# --- System configuration owned by the package (Option A) ---
# php-fpm: dedicated MurOS pool, sized for an appliance, on the socket the
# generated lighttpd config already uses. A first-boot unit may re-tune it.
install -d "$DEST/etc/php/8.4/fpm/pool.d"
cat > "$DEST/etc/php/8.4/fpm/pool.d/muros.conf" <<'POOL'
; MurOS php-fpm pool. Owned by the muros package, do not edit by hand;
; muros-firstboot may re-tune pm.max_children from available memory.
[www]
user = www-data
group = www-data
listen = /run/php/php8.4-fpm.sock
listen.owner = www-data
listen.group = www-data
pm = dynamic
pm.max_children = 16
pm.start_servers = 4
pm.min_spare_servers = 3
pm.max_spare_servers = 8
pm.max_requests = 500
request_terminate_timeout = 120
POOL

# sysctl: routing forwarding for a firewall.
install -d "$DEST/etc/sysctl.d"
cat > "$DEST/etc/sysctl.d/muros.conf" <<'SCTL'
# MurOS firewall forwarding defaults (owned by the muros package).
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
SCTL

# lighttpd: MurOS web UI front end. Shipped under the MurOS prefix and wired
# in with a systemd drop-in, so it never collides with the stock lighttpd
# conffile and the UI does not depend on interface assignment to come up.
install -d "$DEST/usr/local/etc/muros"
cat > "$DEST/usr/local/etc/muros/lighttpd.conf" <<'LIGHTTPD'
server.modules = ( "mod_access","mod_alias","mod_redirect","mod_rewrite","mod_setenv","mod_fastcgi","mod_openssl","mod_deflate","mod_expire" )
server.document-root = "/usr/local/www/"
server.port = 80
server.tag = "MurOS"
server.errorlog = "/var/log/lighttpd/error.log"
index-file.names = ( "index.php","index.html" )
mimetype.assign = ( ".html"=>"text/html",".htm"=>"text/html",".css"=>"text/css",".js"=>"application/javascript",".json"=>"application/json",".png"=>"image/png",".jpg"=>"image/jpeg",".jpeg"=>"image/jpeg",".gif"=>"image/gif",".svg"=>"image/svg+xml",".ico"=>"image/x-icon",".woff"=>"font/woff",".woff2"=>"font/woff2",".ttf"=>"font/ttf",".eot"=>"application/vnd.ms-fontobject",".map"=>"application/json",""=>"application/octet-stream" )
alias.url += ( "/ui/" => "/usr/local/opnsense/www/", "/api/" => "/usr/local/opnsense/www/" )
url.rewrite-if-not-file = ( "^/ui/([^\?]+)(\?(.*))?" => "/ui/index.php?$3", "^/api/([^\?]+)(\?(.*))?" => "/api/api.php?$3" )
fastcgi.server = ( ".php" => ( "localhost" => ( "socket" => "/run/php/php8.4-fpm.sock", "broken-scriptfilename" => "enable" ) ) )
$SERVER["socket"] == ":443" {
  ssl.engine = "enable"
  ssl.pemfile = "/usr/local/etc/muros/server.crt"
  ssl.privkey = "/usr/local/etc/muros/server.key"
}
LIGHTTPD

install -d "$DEST/etc/systemd/system/lighttpd.service.d"
cat > "$DEST/etc/systemd/system/lighttpd.service.d/muros.conf" <<'DROPIN'
# Point lighttpd at the MurOS web UI configuration instead of the stock one.
[Service]
ExecStart=
ExecStart=/usr/sbin/lighttpd -D -f /usr/local/etc/muros/lighttpd.conf
DROPIN

echo "staged into $DEST"
