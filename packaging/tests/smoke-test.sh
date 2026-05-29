#!/usr/bin/env bash
# Smoke test post-install MurOS : verifie que les services tournent et que
# l'API repond. A lancer en root sur le firewall apres le paquet muros.
set -uo pipefail

PASS=0
FAIL=0
fail() { echo "  KO : $1"; FAIL=$((FAIL+1)); }
pass() { echo "  OK : $1"; PASS=$((PASS+1)); }

check_systemd_active() {
  local unit=$1
  if systemctl is-active "$unit" >/dev/null 2>&1; then
    pass "$unit actif"
  else
    fail "$unit pas actif"
  fi
}

echo "=== Smoke test MurOS ==="
echo
echo "Services systemd :"
check_systemd_active muros-backend
check_systemd_active nginx
check_systemd_active ssh
check_systemd_active nftables
# muros-boot.service est oneshot donc is-active = inactive (normal)
if systemctl is-enabled muros-boot.service >/dev/null 2>&1; then
  pass "muros-boot.service enabled"
else
  fail "muros-boot.service pas enabled"
fi

echo
echo "Sockets en ecoute :"
ss -tlnp 2>/dev/null | grep -q ':443 ' && pass "nginx HTTPS sur 443" || fail "nginx pas sur 443"
ss -tlnp 2>/dev/null | grep -q ":$(grep -E '^Port ' /etc/ssh/sshd_config.d/muros.conf 2>/dev/null | awk '{print $2}' || echo 22) " && pass "sshd en ecoute" || fail "sshd pas en ecoute"
ss -tlnp 2>/dev/null | grep -q ':8000 ' && pass "backend MurOS sur 8000" || fail "backend pas sur 8000"

echo
echo "API /api/health :"
if curl -sk -m 5 https://localhost/api/health | grep -q '"status"'; then
  pass "API repond sur HTTPS"
else
  fail "API ne repond pas sur https://localhost/api/health"
fi

echo
echo "Filesystem :"
[ -f /var/lib/muros/muros.db ] && pass "DB SQLite presente" || fail "DB absente"
[ -f /var/lib/muros/muros-secret.key ] && pass "clef JWT presente" || fail "clef JWT absente"
[ -f /etc/nginx/sites-enabled/muros.conf ] && pass "site nginx active" || fail "site nginx absent"

echo
echo "Forwarding IP :"
val=$(sysctl -n net.ipv4.ip_forward 2>/dev/null)
[ "$val" = "1" ] && pass "net.ipv4.ip_forward=1" || fail "net.ipv4.ip_forward=$val (attendu 1)"

echo
echo "=== Resultat : $PASS OK, $FAIL KO ==="
exit $FAIL
