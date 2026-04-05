#!/bin/bash

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root: sudo bash scripts/stop.sh"
  exit 1
fi

echo ""
echo "=== Stopping Padel Access Control ==="
systemctl stop padel-tunnel 2>/dev/null || true
systemctl stop padel-access 2>/dev/null || true
systemctl disable padel-access padel-tunnel 2>/dev/null || true
echo "  ✓ Stopped and disabled."
echo ""
