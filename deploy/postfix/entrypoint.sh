#!/bin/sh
# Postfix + OpenDKIM entrypoint.
#
# On first start (no key present) it generates a DKIM keypair for
# retellis.com / selector ``retellis`` and prints the public-key TXT record that
# must be added to DNS. Then it starts OpenDKIM (signing milter) and Postfix in
# the foreground. If OpenDKIM fails, Postfix still sends — milter_default_action
# =accept in main.cf degrades to unsigned mail rather than blocking the send.
set -eu

DOMAIN=retellis.com
SELECTOR=retellis
KEYDIR=/etc/opendkim/keys/$DOMAIN
KEYFILE="$KEYDIR/$SELECTOR.private"

if [ ! -f "$KEYFILE" ]; then
  echo "=============================================================="
  echo "Generating DKIM keypair for $DOMAIN (selector $SELECTOR)..."
  mkdir -p "$KEYDIR"
  opendkim-genkey -D "$KEYDIR" -d "$DOMAIN" -s "$SELECTOR"
  chown -R opendkim:opendkim /etc/opendkim 2>/dev/null || true
  chmod 600 "$KEYFILE"
  echo
  echo "ADD THIS TXT RECORD TO DNS for $SELECTOR._domainkey.$DOMAIN :"
  echo "--------------------------------------------------------------"
  cat "$KEYDIR/$SELECTOR.txt"
  echo "--------------------------------------------------------------"
  echo "(The record above is also kept at $KEYDIR/$SELECTOR.txt.)"
  echo "=============================================================="
else
  chown -R opendkim:opendkim /etc/opendkim 2>/dev/null || true
fi

# Start the DKIM signing milter (background). Postfix connects on :12345.
opendkim -c /etc/opendkim.conf || echo "[entrypoint] opendkim failed to start — Postfix will send unsigned (milter_default_action=accept)." &

# Give the milter a moment to bind before Postfix starts.
sleep 1

# Postfix in the foreground (container lifecycle tied to it).
exec postfix start-fg