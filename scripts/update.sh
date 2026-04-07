#!/bin/bash
set -e

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root: sudo bash scripts/update.sh"
  exit 1
fi

INSTALL_DIR=/opt/padel-access
VENV_PIP="$INSTALL_DIR/venv/bin/pip"
VENV_PY="$INSTALL_DIR/venv/bin/python"

cd "$INSTALL_DIR"

echo ""
echo "=== Updating Padel Access Control ==="

# ─── 1. Stash local changes (if any) ──────────────
echo ""
echo "[1/6] Checking for local changes..."
STASH_REF=""
if ! git -C "$INSTALL_DIR" diff --quiet || ! git -C "$INSTALL_DIR" diff --cached --quiet; then
  STASH_MSG="update.sh autostash $(date '+%Y-%m-%d %H:%M:%S')"
  git -C "$INSTALL_DIR" stash push --include-untracked -m "$STASH_MSG"
  STASH_REF="$STASH_MSG"
  echo "    ✓ Local changes stashed: '$STASH_MSG'"
  echo "      Restore later with: git -C $INSTALL_DIR stash list"
else
  echo "    ✓ Working tree clean"
fi

# ─── 2. Record current commit (for diff) ──────────
OLD_COMMIT=$(git -C "$INSTALL_DIR" rev-parse HEAD)

# ─── 3. Pull latest ───────────────────────────────
echo ""
echo "[2/6] Pulling latest from origin..."
BRANCH=$(git -C "$INSTALL_DIR" rev-parse --abbrev-ref HEAD)
git -C "$INSTALL_DIR" fetch --quiet origin "$BRANCH"
git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"
NEW_COMMIT=$(git -C "$INSTALL_DIR" rev-parse HEAD)

if [ "$OLD_COMMIT" = "$NEW_COMMIT" ]; then
  echo "    ✓ Already up to date ($NEW_COMMIT)"
  ALREADY_UP_TO_DATE=true
else
  echo "    ✓ Updated $OLD_COMMIT -> $NEW_COMMIT"
  ALREADY_UP_TO_DATE=false
fi

# ─── 4. Update Python deps if requirements.txt changed ──
echo ""
echo "[3/6] Checking Python dependencies..."
if [ "$ALREADY_UP_TO_DATE" = "false" ] && \
   git -C "$INSTALL_DIR" diff --name-only "$OLD_COMMIT" "$NEW_COMMIT" | grep -q '^requirements\.txt$'; then
  echo "    requirements.txt changed — installing..."
  "$VENV_PIP" install --upgrade pip --quiet
  "$VENV_PIP" install -r "$INSTALL_DIR/requirements.txt" --quiet
  echo "    ✓ Python deps updated"
else
  echo "    ✓ requirements.txt unchanged — skipping pip install"
fi

# ─── 5. Sync systemd unit files if they changed ───
echo ""
echo "[4/6] Checking systemd unit files..."
SYSTEMD_CHANGED=false
for unit in padel-access.service padel-tunnel.service; do
  if [ -f "$INSTALL_DIR/systemd/$unit" ]; then
    if ! cmp -s "$INSTALL_DIR/systemd/$unit" "/etc/systemd/system/$unit"; then
      cp "$INSTALL_DIR/systemd/$unit" "/etc/systemd/system/$unit"
      echo "    ✓ Updated /etc/systemd/system/$unit"
      SYSTEMD_CHANGED=true
    fi
  fi
done
if [ "$SYSTEMD_CHANGED" = "true" ]; then
  systemctl daemon-reload
  echo "    ✓ systemd reloaded"
else
  echo "    ✓ systemd units unchanged"
fi

# ─── 6. Compile-check before restart ──────────────
echo ""
echo "[5/6] Compile-checking Python sources..."
"$VENV_PY" -m compileall -q "$INSTALL_DIR/main.py" "$INSTALL_DIR/app"
echo "    ✓ Compile OK"

# ─── 7. Restart services ──────────────────────────
# (DB schema migration runs automatically inside main.py:init_db on startup.)
echo ""
echo "[6/6] Restarting services..."
systemctl restart padel-access
sleep 2
if [ "$SYSTEMD_CHANGED" = "true" ]; then
  systemctl restart padel-tunnel
fi

ACCESS_STATE=$(systemctl is-active padel-access || true)
TUNNEL_STATE=$(systemctl is-active padel-tunnel || true)
echo "    padel-access: $ACCESS_STATE"
echo "    padel-tunnel: $TUNNEL_STATE"

if [ "$ACCESS_STATE" != "active" ]; then
  echo ""
  echo "  ✗ padel-access did not start cleanly. Recent logs:"
  journalctl -u padel-access --no-pager -n 30
  exit 1
fi

# ─── Done ─────────────────────────────────────────
echo ""
echo "=== Update complete ==="
if [ -n "$STASH_REF" ]; then
  echo ""
  echo "  Note: your local changes were stashed."
  echo "        Restore with:  git -C $INSTALL_DIR stash pop"
fi
echo ""
echo "  App logs: journalctl -u padel-access -f"
echo ""
