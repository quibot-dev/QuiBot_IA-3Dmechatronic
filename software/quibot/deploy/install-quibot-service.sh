#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd "$script_dir/.." && pwd)"
service_user="${SUDO_USER:-$(id -un)}"
if [[ "$service_user" == "root" ]]; then
    service_user="quibot"
fi
service_group="$(id -gn "$service_user")"
unit_template="$project_dir/deploy/quibot.service"
unit_target="/etc/systemd/system/quibot.service"
unit_rendered="$(mktemp)"
trap 'rm -f "$unit_rendered"' EXIT

sed \
    -e "s|@PROJECT_DIR@|$project_dir|g" \
    -e "s|@SERVICE_USER@|$service_user|g" \
    -e "s|@SERVICE_GROUP@|$service_group|g" \
    "$unit_template" > "$unit_rendered"

install -o root -g root -m 0644 "$unit_rendered" "$unit_target"
chmod 0755 "$project_dir/deploy/wait-for-quibot-devices.sh"
systemctl daemon-reload
systemctl enable --now quibot.service

echo "Quibot service installed, enabled and started."
