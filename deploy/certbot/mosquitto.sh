#!/bin/sh
# /etc/letsencrypt/renewal-hooks/deploy/mosquitto.sh (mode 755): copies the renewed web
# certificate for Mosquitto's 8883 listener (#31) and restarts it; the nodes reconnect by
# themselves. Replace example.org with the certificate's name.
set -e
case "$RENEWED_LINEAGE" in */example.org) ;; *) exit 0 ;; esac
install -o root -g mosquitto -m 640 "$RENEWED_LINEAGE/fullchain.pem" "$RENEWED_LINEAGE/privkey.pem" /etc/mosquitto/certs/
systemctl restart mosquitto
