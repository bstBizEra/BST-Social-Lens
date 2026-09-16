#!/usr/bin/env bash
# Install/refresh the bst-lens-api systemd unit on bizera-wsl (runbook 2a). Idempotent.
set -euo pipefail
A=/mnt/c/laragon/www/BST-Social-Lens/services/lens-api
H=/home/vily/lens-api-local
mkdir -p "$H"
sed 's/\r$//' "$A/run-service.sh" > "$H/run-service.sh"; chmod +x "$H/run-service.sh"
sed 's/\r$//' "$A/lens-api-local.sh" > "$H/lens-api-local.sh"; chmod +x "$H/lens-api-local.sh"
sed 's/\r$//' "$A/bst-lens-api.service" | sudo tee /etc/systemd/system/bst-lens-api.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable bst-lens-api.service >/dev/null 2>&1 || true
sudo systemctl restart bst-lens-api.service
sleep 5
echo "active:  $(systemctl is-active bst-lens-api)"
echo "enabled: $(systemctl is-enabled bst-lens-api)"
curl -s http://127.0.0.1:7710/health; echo
journalctl -u bst-lens-api -n 3 --no-pager | cut -c1-140
