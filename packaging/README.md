# MurOS packaging

Contient les fichiers de configuration systeme livres avec MurOS pour un
deploiement sur Debian/Ubuntu.

## Layout cible sur le boitier

```
/opt/muros/                 sources de l'application
  backend/                   API Python (uvicorn)
  frontend/dist/             SPA build statique (servie par nginx ou autre)
/var/lib/muros/             etat persistant
  muros.db                  SQLite
  backups/                   snapshots de configuration
  updates_state.json         cache des MAJ apt
/etc/muros/                 fichiers de conf generes par MurOS
  nftables.conf              dernier ruleset applique
  routes.conf                script bash de restauration des routes statiques
```

## Persistance au reboot

MurOS s'appuie sur la base SQLite `/var/lib/muros/muros.db` comme
source of truth. Au boot, `muros-boot.service` (oneshot, avant
`muros-backend.service`) execute `scripts/muros_boot.py` qui rejoue :

1. Creation des interfaces VLAN au noyau (`ip link add ... type vlan`)
2. Application IP/MTU/state sur toutes les interfaces enregistrees
3. Reapplication des routes statiques activees
4. Chargement du ruleset nftables compile depuis la DB (regles, NAT, zones)

Les autres composants sont persistants nativement :

| Composant | Persistance |
|---|---|
| Sysctl hardening | `/etc/sysctl.d/99-muros-hardening.conf` charge par sysctl --system |
| NTP | `/etc/systemd/timesyncd.conf.d/muros.conf` lu par systemd-timesyncd |
| DNS | `/etc/systemd/resolved.conf.d/muros.conf` lu par systemd-resolved |
| HA VRRP | `/etc/keepalived/keepalived.conf` lu par keepalived |
| HA conntrack | `/etc/conntrackd/conntrackd.conf` lu par conntrackd |
| HA notify | `/usr/lib/muros/ha-notify.sh` appele a chaque transition VRRP |
| fail2ban | `/etc/fail2ban/jail.d/muros.local` + filter charge par fail2ban-server |
| SSH | `/etc/ssh/sshd_config.d/muros.conf` lu par sshd |
| Utilisateurs MurOS | DB SQLite |
| Sauvegardes locales | Fichiers tar.gz dans `/var/lib/muros/backups/` |
| Config backup distant | `/var/lib/muros/backups/remote.json` |
| Cle SSH backup | Fichier dans `/var/lib/muros/ssh/` (configurable) |

## Installation rapide (recommande)

Via le `.deb` publie en release GitHub :

```bash
curl -fsSL https://github.com/murosorg/muros/releases/latest/download/install.sh | sudo bash
```

Le paquet installe les dependances apt (nftables, nginx, fail2ban, wireguard,
strongswan, etc.), deploie le backend dans `/opt/muros/backend/`, construit
le venv Python sur la cible, copie le frontend dans `/opt/muros/web/`, active
les units systemd et demarre le backend.

## Desinstallation

Une seule methode officielle, symetrique de l'install :

```bash
curl -fsSL https://github.com/murosorg/muros/releases/latest/download/uninstall.sh | sudo bash
```

Le script stoppe et disable les services MurOS et optionnels (keepalived/
strongswan/wireguard/snmpd), purge le paquet (meme si dpkg est en etat
incoherent grace au fallback `--force-remove-reinstreq`), nettoie les
drop-ins ecrits hors paquet (`/etc/swanctl/conf.d/muros.conf`,
`/etc/wireguard/wg0.conf`, etc.), restaure `/etc/network/interfaces`
depuis la sauvegarde, et unmask les services reseau natifs
(`systemd-networkd`, `NetworkManager`, ...).

Variable optionnelle `MUROS_PURGE_DEPS=1` pour purger en plus les paquets
de dependances (keepalived, strongswan, wireguard-tools, snmpd, fail2ban).
Par defaut ils sont conserves, ils peuvent servir hors MurOS.

Le drop-in SSH n'est **pas** active par defaut (risque de lock-out, voir
section dediee plus bas).

## Installation pas a pas

### 1. Installer les paquets systeme

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip nftables nginx ssl-cert fail2ban rsync
```

Les notifications mail passent en SMTP DIRECT depuis MurOS vers le
smarthost configure dans l'UI (Notifications). Plus de daemon postfix
local, plus de question debconf a presseder.

Les paquets de haute disponibilite (`keepalived` et `conntrackd`) ne sont
pas obligatoires au premier demarrage. La page Haute disponibilite de l'UI
expose un bouton "Installer maintenant" qui declenche
`apt-get install -y keepalived conntrackd` (operation idempotente). Il est
aussi possible de les installer manuellement :

```bash
sudo apt install -y keepalived conntrackd
```

Pas besoin d'installer `chrony` ni `unbound` : MurOS s'appuie sur
`systemd-timesyncd` (NTP) et `systemd-resolved` (DNS) deja livres avec
Debian 13 via le paquet `systemd`. Les drop-ins generes par MurOS
posent uniquement `NTP=` et `DNS=`, le reste de la conf reste celle de
Debian.

Le paquet `ssl-cert` installe automatiquement une paire de certificats
snakeoil (`/etc/ssl/certs/ssl-cert-snakeoil.pem` + `/etc/ssl/private/ssl-cert-snakeoil.key`)
utilises par nginx en attendant qu'un vrai cert soit deploye.

### 2. Deposer les sources

```bash
sudo mkdir -p /opt/muros /var/lib/muros
sudo cp -r backend frontend /opt/muros/

# Backend : creer le venv et installer les deps
cd /opt/muros/backend
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt

# Frontend : build statique (necessite Node localement, sinon builder ailleurs)
cd /opt/muros/frontend
sudo npm ci
sudo npm run build
# Produit /opt/muros/frontend/dist consomme par nginx
```

### 3. Deposer les fichiers de conf et activer

```bash
sudo cp -r packaging/etc/* /etc/

# Recharger systemd et journald
sudo systemctl daemon-reload
sudo systemctl restart systemd-journald

# Activer le service de boot (rejoue interfaces/routes/nftables au demarrage)
sudo systemctl enable muros-boot.service

# Le cert TLS snakeoil est deja en place via le paquet ssl-cert.
# Pour le regenerer (par exemple apres changement de hostname) :
sudo make-ssl-cert generate-default-snakeoil --force-overwrite

# Activer le site nginx
sudo ln -s /etc/nginx/sites-available/muros /etc/nginx/sites-enabled/muros
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

# Demarrer le backend MurOS
sudo systemctl enable --now muros-backend.service
```

### 4. Acceder a l'UI

Ouvrir `https://<ip-du-boitier>/` (accepter le cert self-signed la
premiere fois). Le login par defaut est `admin` / `muros`. MurOS force
un changement de mot de passe au premier login (`must_change_password=true`
en base).

> **ATTENTION SSH** : le drop-in `/etc/ssh/sshd_config.d/muros.conf`
> desactive l'authentification par mot de passe (`PasswordAuthentication
> no`) et bloque le login root. Si vous travaillez sur un Debian frais sans
> cle SSH deja deposee dans `~/.ssh/authorized_keys` de l'utilisateur
> d'administration, **n'activez pas ce drop-in tant que vous n'avez pas
> verifie votre acces par cle**. Procedure recommandee :
>
> 1. Creer le user d'admin et deposer sa cle publique :
>    ```bash
>    sudo adduser walladmin
>    sudo mkdir -p /home/walladmin/.ssh
>    sudo cp /chemin/vers/cle.pub /home/walladmin/.ssh/authorized_keys
>    sudo chown -R walladmin:walladmin /home/walladmin/.ssh
>    sudo chmod 700 /home/walladmin/.ssh
>    sudo chmod 600 /home/walladmin/.ssh/authorized_keys
>    ```
> 2. Tester `ssh walladmin@<ip>` depuis un autre poste, dans un terminal
>    separe **en gardant la session console ouverte**.
> 3. Seulement ensuite, copier le drop-in et redemarrer sshd :
>    ```bash
>    sudo cp packaging/etc/ssh/sshd_config.d/muros.conf /etc/ssh/sshd_config.d/
>    sudo systemctl restart sshd
>    ```

## Fichiers fournis

### `/etc/systemd/system/muros-backend.service`
Unite systemd qui lance l'API uvicorn en root, ecoute sur 127.0.0.1:8000
avec MUROS_APPLY=true. A mettre derriere un nginx en frontal pour TLS.

### `/etc/systemd/journald.conf.d/muros.conf`
Limite la retention du journal :
- 500 Mo max, 200 Mo gardes libres
- 50 Mo par fichier, rotation hebdomadaire
- 1 mois de retention totale

Le firewall peut logger beaucoup si plusieurs regles ont `log` actif,
cette borne evite de saturer le disque.

### `/etc/logrotate.d/muros`
Reserve pour d'eventuels fichiers log texte (ex : access logs). MurOS
lui-meme passe par journald, donc cette rotation est inactive par defaut.

### `/etc/nginx/sites-available/muros`
Frontal HTTPS qui :
- Sert le build statique du frontend depuis `/opt/muros/frontend/dist`
- Reverse-proxy `/api/*` vers `127.0.0.1:8000` (le backend uvicorn)
- Redirige tout HTTP vers HTTPS
- Headers de securite : HSTS, X-Frame-Options DENY, Referrer-Policy
- Rate limit basique sur `/api/auth/login` (5 req/s, burst 10) anti-bruteforce
- Le frontend etant statique, **aucun service systemd dedie n'est requis
  pour lui** : nginx (deja un service standard) suffit.

## Hardening systeme

### `/etc/sysctl.d/99-muros-hardening.conf`
Durcissement noyau pose au packaging : anti SYN flood, anti spoofing (rp_filter),
blocage des redirects et source-routing, ignore les broadcast ICMP, log des
paquets martians. Active automatiquement au boot (`sysctl --system`).

L'UI Systeme > Hardening permet de revisiter ces valeurs sans editer le fichier.

### `/etc/fail2ban/jail.d/muros.local` + `/etc/fail2ban/filter.d/muros-api.conf`
MurOS s'appuie sur la jail `[sshd]` fournie par le paquet `fail2ban` Debian
(active par defaut) et ajoute une seule jail custom :
- **muros-api** : 5 echecs de login MurOS en 5 min => ban 30 min. Le filtre
  parse les warnings `muros.auth auth failed for <user> from <ip>` que le
  backend emet dans journald, plus les 401 du access log nginx.

Action de ban : `nftables-multiport` (table `inet f2b-table` dediee, distincte
du ruleset MurOS).

```bash
# Voir les bans actifs
sudo fail2ban-client status sshd
sudo fail2ban-client status muros-api

# Debannir une IP
sudo fail2ban-client set muros-api unbanip 1.2.3.4
```

### `/etc/ssh/sshd_config.d/muros.conf`
Drop-in SSH :
- PasswordAuthentication off (cle obligatoire)
- PermitRootLogin no
- Aucun forwarding (TCP/agent/X11/tunnel)
- MaxAuthTries 3, ClientAliveInterval 5 min

**A faire manuellement** : decommenter les directives `Match Address` en bas
du fichier pour restreindre SSH a une IP/sous-reseau de management, puis
ajouter le user d'admin au groupe `muros-admin` :

```bash
sudo groupadd muros-admin
sudo usermod -aG muros-admin <user-admin>
sudo systemctl restart sshd
```
