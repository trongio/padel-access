#!/bin/bash

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root: sudo bash scripts/restart.sh"
  exit 1
fi

echo "=== Restarting Padel Access Control ==="
systemctl restart padel-access
systemctl restart padel-tunnel
echo "  ✓ Restarted."
echo "  App logs:    journalctl -u padel-access -f"
echo ""
