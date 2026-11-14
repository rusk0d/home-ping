# Server configuration

The files a rebuilt server needs besides the backend itself, copied from the running one
(Ubuntu 24.04, 2026-11-14) with the host name replaced by `example.org`. They are
reference copies: nothing installs them automatically, and after changing one on the
server, change it here in the same PR.

| File here | Goes to | What |
|---|---|---|
| `mosquitto/mosquitto.conf` | `/etc/mosquitto/mosquitto.conf` | three listeners: 1883 and 9001 on localhost, 8883 public with TLS |
| `mosquitto/acl.example` | `/etc/mosquitto/acl` | what each account may read and write |
| `nginx/home-ping.conf` | `/etc/nginx/sites-available/<domain>` | the site: web page, `/mqtt`, `/api/` |
| `nginx/home-ping-limits.conf` | `/etc/nginx/conf.d/` | rate limit zone for `/mqtt` |
| `certbot/mosquitto.sh` | `/etc/letsencrypt/renewal-hooks/deploy/` | copies the renewed certificate to Mosquitto |
| `../web/nginx/security-headers.conf` | `/etc/nginx/snippets/home-ping-headers.conf` | CSP, HSTS and the other headers |
| `../backend/nginx/home-ping-metrics.conf` | `/etc/nginx/sites-available/` | the Tailscale-only metrics site |
| `../backend/systemd/*` | `/etc/systemd/system/` | backend, backup and restore-drill units |
| `../backend/fail2ban/*` | `/etc/fail2ban/filter.d/`, `jail.d/` | bans after wrong API logins |

## Rebuilding the server, in order

1. Packages: `apt install nginx mosquitto mosquitto-clients fail2ban python3-venv sqlite3`, and
   certbot with its nginx plugin (not from apt on the current server).
2. Firewall: `ufw allow 22/tcp`, `80/tcp`, `443/tcp`, `8883/tcp`, then `ufw enable`. Nothing
   else is open; the metrics site is reached over Tailscale only (`9105/tcp on tailscale0`).
3. nginx: the site, the limits file and the headers snippet from the table, then
   `certbot --nginx -d <domain> -d www.<domain>`, which adds the TLS lines.
4. Mosquitto: `mosquitto.conf` and the ACL; create the accounts with
   `mosquitto_passwd /etc/mosquitto/passwd <name>` (the first one with `-c`), and
   `/etc/mosquitto/passwd-nodes` with the `sensor` account only. Owner `mosquitto` for all
   three files; mode 600 for the password files, 640 for the ACL. Install the certbot hook and run it once by hand with
   `RENEWED_LINEAGE=/etc/letsencrypt/live/<domain>` so `/etc/mosquitto/certs/` exists.
5. Backend: README "Production setup", then `backend/deploy.sh` from then on.
6. fail2ban: the two files from `backend/fail2ban/`, then `systemctl restart fail2ban`.
7. Web page: copy `web/` to the site's root and create `config.js` from `config.example.js`.
8. Restore the data: `backend/backup_db.py` and the restore drill (README "Backups").

## What is not in the repo and must be kept somewhere safe

These exist only on the server (and the nodes); without them a rebuild means new
credentials everywhere. A password manager entry for each is enough.

- `/opt/iot/.env`: the API login (`DASH_USER`/`DASH_PASS`), the MQTT account of `backend`, and
  the off-site backup's bucket and keys (`BACKUP_S3_BUCKET`, `AWS_*`, #122).
- `/opt/iot/firebase_service_account.json`: can be re-issued in the Firebase console.
- `/etc/mosquitto/passwd` and `passwd-nodes`: only hashes; keep the passwords themselves.
  The `sensor` password is also in each node's `secrets.h`.
- The database: the nightly off-site backup (README "Backups").
