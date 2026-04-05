#!/bin/bash
set -e

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root: sudo bash scripts/start.sh"
  exit 1
fi

echo ""
echo "=== Starting Padel Access Control ==="
systemctl enable padel-access padel-tunnel
systemctl start padel-access
sleep 2
systemctl start padel-tunnel

echo ""
echo "  ✓ padel-access: $(systemctl is-active padel-access)"
echo "  ✓ padel-tunnel: $(systemctl is-active padel-tunnel)"
echo ""
echo "  App logs:    journalctl -u padel-access -f"
echo "  Tunnel logs: journalctl -u padel-tunnel -f"

# If using quick tunnel, surface the public URL
if [ -z "$(grep CF_TUNNEL_TOKEN /opt/padel-access/.env | cut -d= -f2 | tr -d ' ')" ]; then
  echo ""
  echo "  Quick tunnel active — fetching public URL..."
  sleep 3
  TUNNEL_URL=$(journalctl -u padel-tunnel --no-pager -n 50 2>/dev/null | grep -o 'https://.*\.trycloudflare\.com' | tail -1)
  if [ -n "$TUNNEL_URL" ]; then
    echo "  Public URL: $TUNNEL_URL"
  else
    echo "  URL not ready yet — run: journalctl -u padel-tunnel | grep trycloudflare.com"
  fi
fi
echo ""
