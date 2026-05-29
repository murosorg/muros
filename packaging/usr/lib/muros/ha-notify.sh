#!/bin/bash
set -u
TYPE="${1:-}"; NAME="${2:-}"; STATE="${3:-}"
logger -t muros-ha "keepalived notify: type=$TYPE name=$NAME state=$STATE"

# Ecrit l'etat VRRP dans /run/muros/vrrp-state pour que le backend
# MurOS sache s'il est MASTER ou BACKUP (utilise par ha_sync pour
# autoriser/refuser les ecritures).
mkdir -p /run/muros
echo "$NAME $STATE" > /run/muros/vrrp-state
chmod 0644 /run/muros/vrrp-state
if ! command -v conntrackd >/dev/null 2>&1; then
    logger -t muros-ha "conntrackd absent, transition $STATE sans sync"
    exit 0
fi
case "$STATE" in
    MASTER)
        conntrackd -c
        conntrackd -f
        conntrackd -R
        conntrackd -B
        ;;
    BACKUP|FAULT)
        conntrackd -t
        conntrackd -n
        ;;
    *)
        logger -t muros-ha "etat inconnu : $STATE"
        ;;
esac
exit 0
