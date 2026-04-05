#!/bin/bash

echo ""
echo "=== Padel Access Control — Status ==="
echo ""
echo "── padel-access ─────────────────────────────"
systemctl status padel-access --no-pager -l | head -20
echo ""
echo "── padel-tunnel ─────────────────────────────"
systemctl status padel-tunnel --no-pager -l | head -20
echo ""

# Surface tunnel URL if quick tunnel
TUNNEL_URL=$(journalctl -u padel-tunnel --no-pager -n 100 2>/dev/null | grep -o 'https://.*\.trycloudflare\.com' | tail -1)
if [ -n "$TUNNEL_URL" ]; then
  echo "  Public URL: $TUNNEL_URL"
fi

# Show health check
PORT=$(grep APP_PORT /opt/padel-access/.env 2>/dev/null | cut -d= -f2 | tr -d ' ')
PORT="${PORT:-8000}"
echo ""
echo "── Tailscale ────────────────────────────────"
if command -v tailscale &> /dev/null; then
  tailscale status 2>/dev/null || echo "  Tailscale not connected"
else
  echo "  Tailscale not installed"
fi
echo ""
curl -s "http://localhost:${PORT}/api/health" | python3 -m json.tool 2>/dev/null || echo "  App not responding on port $PORT"
echo ""
