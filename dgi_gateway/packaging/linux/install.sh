#!/usr/bin/env bash
# Installation de la passerelle DGI sur un poste Linux du site (Ubuntu/Debian, Raspberry Pi OS).
#   sudo ./packaging/linux/install.sh            # depuis le dossier dgi_gateway
# Idempotent : relancer le script met a jour le programme sans toucher a la cle ni au journal.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/../.." && pwd)"
PREFIX=/opt/dgi-gateway
DATA=/var/lib/dgi-gateway
CONF=/etc/dgi-gateway

[ "$(id -u)" -eq 0 ] || { echo "Lancer avec sudo." >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 (>= 3.10) requis." >&2; exit 1; }
python3 - <<'PY' || { echo "Python >= 3.10 requis." >&2; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY
python3 -m venv --help >/dev/null 2>&1 || { echo "Installez python3-venv (apt install python3-venv)." >&2; exit 1; }

# compte de service sans shell ni mot de passe, membre de dialout pour le port serie
id dgi-gateway >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin dgi-gateway
usermod -a -G dialout dgi-gateway

install -d -m 0755 "$PREFIX"
install -d -m 0700 -o dgi-gateway -g dgi-gateway "$DATA"
# setgid : la configuration ecrite par "sudo dgi-gateway init" reste lisible par le service
install -d -m 2750 -o root -g dgi-gateway "$CONF"

python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/venv/bin/pip" install --quiet --upgrade "$SRC"
install -m 0644 "$SRC/README.md" "$PREFIX/README.md"
ln -sf "$PREFIX/venv/bin/dgi-gateway" /usr/local/bin/dgi-gateway

install -m 0644 "$SRC/packaging/linux/dgi-gateway.service" /etc/systemd/system/dgi-gateway.service
if [ -d /etc/udev/rules.d ] && [ ! -f /etc/udev/rules.d/99-dgi-mcf.rules ]; then
  install -m 0644 "$SRC/packaging/linux/99-dgi-mcf.rules" /etc/udev/rules.d/99-dgi-mcf.rules
fi
[ -f "$CONF/gateway.json" ] && chgrp dgi-gateway "$CONF/gateway.json" && chmod 0640 "$CONF/gateway.json"
systemctl daemon-reload

echo
echo "Programme installe : $("$PREFIX/venv/bin/dgi-gateway" --version)"
"$PREFIX/venv/bin/dgi-gateway" selftest >/dev/null && echo "Autotest sur simulateur : OK"
cat <<EOF

Etapes suivantes (voir $PREFIX/README.md) :
  1. sudo dgi-gateway init --site https://VOTRE-SITE.frappe.cloud --gateway-id GW-XXXX-01
  2. sudo -u dgi-gateway dgi-gateway discover
  3. sudo -u dgi-gateway dgi-gateway enroll --code CODE-AFFICHE-DANS-ERPNEXT
     -> comparer l'empreinte affichee avec celle d'ERPNext, puis approuver dans ERPNext
  4. sudo systemctl enable --now dgi-gateway && journalctl -u dgi-gateway -f
EOF
if systemctl is-active --quiet dgi-gateway; then
  systemctl restart dgi-gateway && echo "Service redemarre avec la nouvelle version."
fi
