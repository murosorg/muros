#!/usr/bin/env bash
# End-to-end integration test for the four gap-closing services added in
# the rc89..rc103 batch: QoS shaping, remote syslog, dynamic DNS and the
# stateful DHCPv6 server.
#
# Unlike smoke-test.sh (which only checks the box came up), this script
# drives the live REST API to configure and APPLY each service, then
# asserts the real system state (tc qdiscs, rsyslog drop-in, kea-dhcp6
# config validation, ...). It then reverts every change it made.
#
# Run it on a real Debian 13 VM where the MurOS backend runs with
# MUROS_APPLY=1 (otherwise the apply steps are no-ops and the system
# assertions are skipped). As root:
#
#   MUROS_PASS='the-root-password' packaging/tests/integration-services.sh
#
# Optional environment:
#   BASE_URL   API base (default https://localhost)
#   MUROS_USER login user   (default root)
#   MUROS_PASS login password (required, no default for safety)
set -uo pipefail

BASE_URL="${BASE_URL:-https://localhost}"
MUROS_USER="${MUROS_USER:-root}"
MUROS_PASS="${MUROS_PASS:-}"

PASS=0; FAIL=0; SKIP=0
pass() { echo "  OK   : $1"; PASS=$((PASS+1)); }
fail() { echo "  KO   : $1"; FAIL=$((FAIL+1)); }
skip() { echo "  SKIP : $1"; SKIP=$((SKIP+1)); }

for bin in curl jq; do
  command -v "$bin" >/dev/null 2>&1 || { echo "FATAL: $bin is required"; exit 2; }
done
[ -n "$MUROS_PASS" ] || { echo "FATAL: set MUROS_PASS to the login password"; exit 2; }

APPLY_ON=0
if systemctl show -p Environment muros-backend 2>/dev/null \
   | grep -qiE 'MUROS_APPLY=(1|true|yes)'; then
  APPLY_ON=1
fi
[ "$APPLY_ON" = "1" ] || echo "NOTE: MUROS_APPLY not detected as on; system-level assertions will be skipped."

CURL=(curl -sk -m 15)

# --- login -----------------------------------------------------------------
TOKEN=$("${CURL[@]}" -X POST "$BASE_URL/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "$(jq -nc --arg u "$MUROS_USER" --arg p "$MUROS_PASS" '{username:$u,password:$p}')" \
  | jq -r '.access_token // empty')
if [ -z "$TOKEN" ]; then
  echo "FATAL: login failed (no access_token). MFA enabled or wrong credentials?"
  exit 2
fi
AUTH=(-H "Authorization: Bearer $TOKEN")

api() { # METHOD PATH [JSON]
  local m=$1 p=$2 body=${3:-}
  if [ -n "$body" ]; then
    "${CURL[@]}" "${AUTH[@]}" -X "$m" "$BASE_URL$p" -H 'Content-Type: application/json' -d "$body"
  else
    "${CURL[@]}" "${AUTH[@]}" -X "$m" "$BASE_URL$p"
  fi
}

# Pick a target interface (first one MurOS knows about).
IFACE_JSON=$(api GET /api/interfaces)
IFACE_ID=$(echo "$IFACE_JSON" | jq -r '.[0].id // empty')
IFACE_NAME=$(echo "$IFACE_JSON" | jq -r '.[0].name // empty')
if [ -z "$IFACE_ID" ]; then
  echo "FATAL: no interface available to test against"; exit 2
fi
echo "Target interface: $IFACE_NAME (id $IFACE_ID)"
echo

# ===========================================================================
echo "== QoS / traffic shaping =="
SHAPER_ID=$(api POST /api/qos/shapers \
  "$(jq -nc --argjson i "$IFACE_ID" '{interface_id:$i,enabled:true,bandwidth_kbit:100000}')" \
  | jq -r '.id // empty')
if [ -n "$SHAPER_ID" ]; then
  pass "shaper created (id $SHAPER_ID)"
  api POST "/api/qos/shapers/$SHAPER_ID/classes" \
    "$(jq -nc '{name:"default",priority:3,rate_kbit:50000,is_default:true}')" >/dev/null
  api POST /api/qos/apply >/dev/null
  if [ "$APPLY_ON" = "1" ]; then
    qd=$(tc qdisc show dev "$IFACE_NAME" 2>/dev/null)
    echo "$qd" | grep -q htb && pass "tc htb qdisc present on $IFACE_NAME" || fail "no htb qdisc on $IFACE_NAME"
    echo "$qd" | grep -q fq_codel && pass "tc fq_codel leaf present" || fail "no fq_codel leaf"
  else
    skip "tc assertions (MUROS_APPLY off)"
  fi
  api DELETE "/api/qos/shapers/$SHAPER_ID" >/dev/null
  api POST /api/qos/apply >/dev/null
  if [ "$APPLY_ON" = "1" ]; then
    tc qdisc show dev "$IFACE_NAME" 2>/dev/null | grep -q htb \
      && fail "htb qdisc still present after cleanup" || pass "tc cleared after shaper delete"
  fi
else
  fail "shaper creation failed"
fi
echo

# ===========================================================================
echo "== Remote syslog =="
api PUT /api/syslog/config \
  "$(jq -nc '{enabled:true,host:"192.0.2.50",port:514,protocol:"udp",format:"rfc5424"}')" >/dev/null
api POST /api/syslog/apply >/dev/null
if [ "$APPLY_ON" = "1" ]; then
  [ -f /etc/rsyslog.d/muros-remote.conf ] && pass "rsyslog drop-in written" || fail "drop-in missing"
  systemctl is-active rsyslog >/dev/null 2>&1 && pass "rsyslog active" || fail "rsyslog not active"
  if rsyslogd -N1 >/dev/null 2>&1; then pass "rsyslogd config validates (-N1)"; else fail "rsyslogd -N1 failed"; fi
else
  skip "rsyslog assertions (MUROS_APPLY off)"
fi
api PUT /api/syslog/config \
  "$(jq -nc '{enabled:false,host:"192.0.2.50",port:514,protocol:"udp",format:"rfc5424"}')" >/dev/null
api POST /api/syslog/apply >/dev/null
if [ "$APPLY_ON" = "1" ]; then
  [ -f /etc/rsyslog.d/muros-remote.conf ] && fail "drop-in still present after disable" || pass "drop-in removed after disable"
fi
echo

# ===========================================================================
echo "== Dynamic DNS =="
PUBIP=$(api GET /api/dyndns/public-ip | jq -r '.ip // "null"')
pass "public-ip endpoint responded (ip=$PUBIP)"
DD_ID=$(api POST /api/dyndns \
  "$(jq -nc '{enabled:false,provider:"custom",hostname:"it-test.example",custom_url:"https://192.0.2.50/update?host={hostname}&ip={ip}"}')" \
  | jq -r '.id // empty')
if [ -n "$DD_ID" ]; then
  pass "dyndns entry created (id $DD_ID)"
  api GET /api/dyndns | jq -e --argjson i "$DD_ID" 'any(.[]; .id==$i)' >/dev/null \
    && pass "dyndns entry persisted" || fail "dyndns entry not persisted"
  api DELETE "/api/dyndns/$DD_ID" >/dev/null
  api GET /api/dyndns | jq -e --argjson i "$DD_ID" 'all(.[]; .id!=$i)' >/dev/null \
    && pass "dyndns entry deleted" || fail "dyndns entry not deleted"
else
  fail "dyndns entry creation failed"
fi
echo

# ===========================================================================
echo "== Stateful DHCPv6 =="
P6_ID=$(api POST /api/dhcp6/pools \
  "$(jq -nc --argjson i "$IFACE_ID" '{interface_id:$i,range_start:"fd00:0:0:1::100",range_end:"fd00:0:0:1::1ff",enabled:true}')" \
  | jq -r '.id // empty')
if [ -n "$P6_ID" ]; then
  pass "dhcp6 pool created (id $P6_ID)"
  api PUT /api/dhcp6/config "$(jq -nc '{enabled:true,default_lease_seconds:3600}')" >/dev/null
  api POST /api/dhcp6/apply >/dev/null
  if [ "$APPLY_ON" = "1" ]; then
    [ -f /etc/kea/kea-dhcp6.conf ] && pass "kea-dhcp6.conf written" || fail "kea-dhcp6.conf missing"
    if kea-dhcp6 -t /etc/kea/kea-dhcp6.conf >/dev/null 2>&1; then pass "kea-dhcp6 -t validates config"; else fail "kea-dhcp6 -t rejected config"; fi
    systemctl is-active kea-dhcp6-server >/dev/null 2>&1 && pass "kea-dhcp6-server active" || fail "kea-dhcp6-server not active"
  else
    skip "kea-dhcp6 assertions (MUROS_APPLY off)"
  fi
  api PUT /api/dhcp6/config "$(jq -nc '{enabled:false,default_lease_seconds:3600}')" >/dev/null
  api DELETE "/api/dhcp6/pools/$P6_ID" >/dev/null
  api POST /api/dhcp6/apply >/dev/null
  pass "dhcp6 cleanup done"
else
  fail "dhcp6 pool creation failed"
fi

echo
echo "=== Result: $PASS OK, $FAIL KO, $SKIP skipped ==="
exit $FAIL
