# Paquet Debian muros

Ce dossier contient les fichiers necessaires pour builder le paquet
Debian `muros` qui packagize le backend FastAPI + le frontend Vite
builde + les units systemd + la conf nginx/fail2ban/sshd/journald.

## Build local (Debian 13 trixie)

```bash
sudo apt install build-essential debhelper devscripts python3-venv nodejs npm
cp -r packaging/debian debian
dpkg-buildpackage -us -uc -b
# -> ../muros_<version>_all.deb
```

## Install / upgrade

```bash
sudo apt install ./muros_1.0.0_all.deb
# upgrade depuis le repo MurOS :
sudo apt update && sudo apt install --only-upgrade muros
```

## Layout

- `/opt/muros/backend/` : code Python + venv embarque (cap_net_admin via setcap)
- `/opt/muros/web/` : build Vite (servi par nginx)
- `/etc/muros/` : conffiles MurOS (secret JWT, nftables.conf...). Conserve a la purge sauf `apt purge`.
- `/var/lib/muros/` : DB SQLite, backups, etat runtime. Conserve a la purge sauf `apt purge`.
- `/lib/systemd/system/muros-{backend,boot,watcher}.service` : units systemd activees au postinst.
- Conffiles : `/etc/nginx/sites-available/muros`, sysctl, fail2ban, sshd, journald.

## Cycle MAJ

1. Sur tag `vX.Y.Z` push, GitHub Actions build le .deb et le publie en release.
2. Le repo apt MurOS (separe) recupere le .deb et l'expose en `stable`.
3. Sur l'appliance, `apt update && apt install muros` (declenche depuis l'UI).
4. Le `postinst` lance les migrations Alembic et restart le service.
5. En cas de probleme : `apt install muros=<ancienne-version>` rollback.

Le snapshot pre-MAJ (DB + nftables.conf) est cree par l'UI MurOS
avant chaque `apt install muros`, pas par le paquet.
