# Adding a managed service

A "managed service" in MurOS is a UI page that owns one (or a small
cluster of) systemd daemon(s) : DHCP / Kea, DNS / unbound, SNMP /
snmpd, WireGuard, IPsec / strongSwan, HA / keepalived + conntrackd,
SSH access, HTTP access, Notifications. This document is the
reference template every new service must follow so the Save / Apply
UX stays uniform and the audit trail keeps working.

## 1. Backend : split write_conf and reload

Under `backend/app/services/<name>_apply.py` (or `backend/app/<name>.py`
for existing layouts) expose three callables :

```python
def write_conf(db: Session) -> None:
    """Render and persist the on-disk config file ONLY. No systemd.

    Called from every Save route AND from `apply()` for back-compat.
    Must be idempotent and safe to call repeatedly.
    """

def reload(db: Session) -> None:
    """Restart / reload the daemon to pick up the on-disk config.

    Validate the rendered config first (e.g. `unbound-checkconf`,
    `kea-dhcp4 -t`, `swanctl --load-conns -n`). Raise a service-
    specific `<Name>ApplyError` when validation fails so the route can
    surface it as a 409 and keep the dirty flag lit.
    """

def apply(db: Session) -> None:
    """Back-compat wrapper used by seed / muros-boot. Just calls
    write_conf then reload in a single shot."""
    write_conf(db); reload(db)
```

Key rule : **no route should ever call `reload()` directly except the
Apply route.** All Save routes go through `write_conf()` + a
`service_dirty.mark_dirty()` call.

## 2. Backend : Save routes flag dirty

Every POST / PUT / DELETE route that mutates the service's DB state
ends with :

```python
from app import service_dirty

@router.put("/config")
def update_config(payload, db: Session = Depends(get_db)):
    cfg = _get_cfg(db)
    # ... mutate cfg ...
    db.commit()
    <name>_apply.write_conf(db)
    service_dirty.mark_dirty(db, "<name>", summary="short human label")
    return cfg
```

Factorise this pair into a `_stage_<name>(db, summary=...)` helper
at the top of the route module (see `_stage_dhcp` in
`backend/app/routes/services.py` for the canonical example).

The `summary` argument is what shows up in the audit log
(`/api/services/log`). Keep it short and human, in present tense :
"DHCP pool added", "SNMP community updated", "WireGuard peer revoked".

## 3. Backend : Apply and Pending routes

Add the two paired endpoints under the service's router. The naming
convention is `/api/<name>/apply` and `/api/<name>/pending`.

```python
@router.get("/pending")
def pending(db: Session = Depends(get_db)):
    return service_dirty.get_state(db, "<name>")

@router.post("/apply")
def apply_now(db: Session = Depends(get_db)):
    try:
        <name>_apply.reload(db)
    except <Name>ApplyError as exc:
        # Leave dirty=True so the orange dot stays lit.
        raise HTTPException(409, str(exc)) from exc
    service_dirty.mark_clean(db, "<name>", summary="<daemon> reload")
    return {"applied": True, **service_dirty.get_state(db, "<name>")}
```

## 4. Service name registration

Add `"<name>"` to `KNOWN_SERVICES` in
`backend/app/service_dirty.py`. The aggregated
`/api/services/pending` route automatically includes it.

## 5. Drift reconciliation on startup

If the daemon loads its drop-in config at OS boot independently of
the MurOS backend (Kea, unbound, snmpd, fail2ban, ...),
add a branch to `service_dirty.reconcile_on_startup` that compares
SHA256 of the rendered conf to the on-disk conf and calls
`mark_clean()` when they match. Otherwise leftover dirty flags from
before a reboot become phantom orange dots in the UI.

If the daemon is restarted by `muros-boot.service`
(WireGuard, IPsec, HA, nftables, network), no reconciliation is
needed : `muros-boot` should already be calling
`service_dirty.mark_clean()` for each service it restored.

## 6. Frontend : ApplyServiceButton in PageHeader

Extend the `ServiceName` union in
`frontend/src/components/ApplyServiceButton.tsx` to include the new
name and wire the matching entry in `ENDPOINTS`.

Then in the page :

```tsx
import ApplyServiceButton from '../components/ApplyServiceButton'

<PageHeader
  title="<Service>"
  description="..."
  status={status && <ServiceStatusInline ... />}
  actions={
    <ApplyServiceButton
      service="<name>"
      pendingTooltip="Restart <daemon> to apply the saved configuration."
      onApplied={() => { void reload(); setMessage('<daemon> reloaded.') }}
      onError={setError}
      disabled={!status?.installed}
      formDirty={cfgDirty}  // optional, suppresses the dot while form is being edited
    />
  }
/>
```

## 7. Frontend : Save button in form / modal

Every editable form keeps its own Save button, with the same orange
dot styling for consistency :

```tsx
<button
  type="button"
  className="btn-primary relative"
  onClick={onSave}
  disabled={busy || !dirty}
>
  {dirty && !busy && (
    <span className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-orange-500 ring-2 ring-white" />
  )}
  {busy ? 'Saving...' : 'Save'}
</button>
```

Net UX flow :
- Edit form -> Save dot lights up, Apply dot suppressed.
- Click Save -> Save dot clears, Apply dot lights up.
- Click Apply -> Apply dot clears, page is in sync.

## 8. Frontend : sidebar nav entry

Add `service: "<name>"` to the matching `navItems` entry in
`frontend/src/components/Layout.tsx`. The sidebar polls
`/api/services/pending` every 5s and renders a small orange dot next
to entries whose service is dirty.

## 9. Checklist

Before merging :

- [ ] `write_conf` / `reload` / `apply` split exists
- [ ] All Save routes call `_stage_<name>` (write_conf + mark_dirty)
- [ ] `/api/<name>/apply` and `/api/<name>/pending` exposed
- [ ] `<name>` added to `KNOWN_SERVICES`
- [ ] Apply route validates conf before reload (raises *ApplyError)
- [ ] `reconcile_on_startup` covers the service if its daemon loads conf at OS boot
- [ ] `ApplyServiceButton` wired in PageHeader.actions
- [ ] Save button in form has the orange dot
- [ ] Sidebar nav entry has `service: "<name>"`
- [ ] Audit summaries are short and human-readable
