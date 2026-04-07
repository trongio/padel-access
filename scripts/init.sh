#!/bin/bash
set -e

# ─── Must run as root ─────────────────────────────
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root: sudo bash scripts/init.sh"
  exit 1
fi

INSTALL_DIR=/opt/padel-access

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Padel Access Control — Init Setup      ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ─── 1. System packages ───────────────────────────
echo "[1/8] Installing system dependencies..."
apt-get update -qq
apt-get install -y \
  python3-pip \
  python3-venv \
  python3-dev \
  python3-smbus \
  i2c-tools \
  libgpiod2 \
  libgpiod-dev \
  libjpeg-dev \
  libfreetype6-dev \
  libopenjp2-7 \
  libssl-dev \
  libffi-dev \
  git \
  curl \
  jq
echo "    ✓ System dependencies installed"

# ─── 2. Enable I2C (Ubuntu — NOT raspi-config) ────
echo "[2/8] Enabling I2C..."
CONFIG_FILE=/boot/firmware/config.txt

# Enable i2c-arm if not already set
if ! grep -q "dtparam=i2c_arm=on" "$CONFIG_FILE"; then
  echo "dtparam=i2c_arm=on" >> "$CONFIG_FILE"
  echo "    ✓ I2C enabled in $CONFIG_FILE (reboot required)"
else
  echo "    ✓ I2C already enabled"
fi

# Load i2c-dev module now and on boot
modprobe i2c-dev 2>/dev/null || true
if ! grep -q "i2c-dev" /etc/modules; then
  echo "i2c-dev" >> /etc/modules
fi

# Add current user to i2c group if exists
if getent group i2c > /dev/null 2>&1; then
  usermod -aG i2c "${SUDO_USER:-root}" 2>/dev/null || true
fi

# ─── 3. Python virtual environment ────────────────
echo "[3/8] Setting up Python virtual environment..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet
echo "    ✓ Python venv ready at $INSTALL_DIR/venv"

# ─── 4. Install cloudflared ───────────────────────
echo "[4/8] Installing cloudflared..."
if ! command -v cloudflared &> /dev/null; then
  ARCH=$(dpkg --print-architecture)
  if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then
    CF_ARCH="arm64"
  elif [ "$ARCH" = "armhf" ]; then
    CF_ARCH="arm"
  else
    CF_ARCH="amd64"
  fi
  curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}" \
    -o /usr/local/bin/cloudflared
  chmod +x /usr/local/bin/cloudflared
  echo "    ✓ cloudflared installed ($(cloudflared --version))"
else
  echo "    ✓ cloudflared already installed ($(cloudflared --version))"
fi

# ─── 5. Interactive .env setup ────────────────────
echo "[5/8] Configuring .env..."
echo ""

if [ -f "$INSTALL_DIR/.env" ]; then
  read -rp "    .env already exists. Overwrite? [y/N]: " OVERWRITE
  if [[ ! "$OVERWRITE" =~ ^[Yy]$ ]]; then
    echo "    Skipping .env setup — keeping existing file."
    ENV_SKIP=true
  fi
fi

if [ -z "$ENV_SKIP" ]; then
  echo "    Leave blank to accept [default] values."
  echo ""

  # API Key
  read -rp "    API Key (Bearer token for REST API) [auto-generate]: " INPUT_API_KEY
  if [ -z "$INPUT_API_KEY" ]; then
    INPUT_API_KEY=$(cat /proc/sys/kernel/random/uuid | tr -d '-')
    echo "    → Generated: $INPUT_API_KEY"
  fi

  # Language
  echo "    Language options: EN (English) / KA (ქართული)"
  read -rp "    Display language [EN]: " INPUT_LANG
  INPUT_LANG="${INPUT_LANG:-EN}"
  INPUT_LANG=$(echo "$INPUT_LANG" | tr '[:lower:]' '[:upper:]')

  # Code length
  read -rp "    Access code length (4-8 digits) [6]: " INPUT_CODE_LENGTH
  INPUT_CODE_LENGTH="${INPUT_CODE_LENGTH:-6}"

  # Display idle text
  read -rp "    Display idle text [AllDigital]: " INPUT_IDLE_TEXT
  INPUT_IDLE_TEXT="${INPUT_IDLE_TEXT:-AllDigital}"

  # App port
  read -rp "    API port [8000]: " INPUT_PORT
  INPUT_PORT="${INPUT_PORT:-8000}"

  # Exit button GPIO
  read -rp "    Exit button GPIO pin [26]: " INPUT_EXIT_GPIO
  INPUT_EXIT_GPIO="${INPUT_EXIT_GPIO:-26}"

  # Buzzer
  read -rp "    Buzzer GPIO pin [24]: " INPUT_BUZZER_GPIO
  INPUT_BUZZER_GPIO="${INPUT_BUZZER_GPIO:-24}"
  read -rp "    Enable buzzer? [Y/n]: " INPUT_BUZZER_ENABLED
  [[ "$INPUT_BUZZER_ENABLED" =~ ^[Nn]$ ]] && INPUT_BUZZER_ENABLED="false" || INPUT_BUZZER_ENABLED="true"

  # Timezone
  read -rp "    Timezone (e.g. Asia/Tbilisi) [UTC]: " INPUT_TZ
  INPUT_TZ="${INPUT_TZ:-UTC}"

  # Cloudflare
  echo ""
  echo "    ── Cloudflare Tunnel ──────────────────────────────────"
  echo "    Leave blank to use a temporary quick tunnel (URL changes on restart)."
  echo "    For a permanent URL: create a tunnel at dash.cloudflare.com"
  echo "    and paste the tunnel token below."
  echo ""
  read -rp "    Cloudflare Tunnel Token [leave blank for quick tunnel]: " INPUT_CF_TOKEN

  # Write .env
  cat > "$INSTALL_DIR/.env" <<EOF
# ─── App ──────────────────────────────────────────
APP_HOST=0.0.0.0
APP_PORT=${INPUT_PORT}
API_KEY=${INPUT_API_KEY}
LOG_LEVEL=INFO
TZ=${INPUT_TZ}
APP_LANG=${INPUT_LANG}
CODE_LENGTH=${INPUT_CODE_LENGTH}
MASK_CODE_INPUT=true

# ─── Display ──────────────────────────────────────
DISPLAY_IDLE_TEXT=${INPUT_IDLE_TEXT}
DISPLAY_IDLE_SUBTEXT=${INPUT_IDLE_SUBTEXT}

# ─── Hardware ─────────────────────────────────────
RELAY_ACTIVE_LOW=true
DOOR_RELAY_GPIO=17
DOOR_UNLOCK_DURATION=5

EXIT_BUTTON_GPIO=${INPUT_EXIT_GPIO}

BUZZER_GPIO=${INPUT_BUZZER_GPIO}
BUZZER_ENABLED=${INPUT_BUZZER_ENABLED}

LIGHT_RELAY_1_GPIO=27
LIGHT_RELAY_2_GPIO=22

KEYPAD_ROW_PINS=5,6,13,19
KEYPAD_COL_PINS=12,16,20

# ─── Cloudflare Tunnel ────────────────────────────
CF_TUNNEL_TOKEN=${INPUT_CF_TOKEN}
CF_TUNNEL_NAME=padel-access
EOF

  echo ""
  echo "    ✓ .env written to $INSTALL_DIR/.env"
fi

# ─── 6. Create directories ────────────────────────
echo "[6/8] Creating directories..."
mkdir -p "$INSTALL_DIR/logs"
mkdir -p "$INSTALL_DIR/data"
chmod 750 "$INSTALL_DIR/data"
echo "    ✓ logs/ and data/ created"

# ─── 7. Install systemd services ─────────────────
echo "[7/8] Installing systemd services..."
cp "$INSTALL_DIR/systemd/padel-access.service" /etc/systemd/system/
cp "$INSTALL_DIR/systemd/padel-tunnel.service" /etc/systemd/system/
systemctl daemon-reload
echo "    ✓ systemd services installed"

# ─── 8. Tailscale ────────────────────────────────
echo "[8/8] Installing Tailscale (SSH remote access)..."
echo ""
read -rp "    Install Tailscale for remote SSH access? [Y/n]: " INSTALL_TAILSCALE
if [[ ! "$INSTALL_TAILSCALE" =~ ^[Nn]$ ]]; then
  if ! command -v tailscale &> /dev/null; then
    curl -fsSL https://tailscale.com/install.sh | sh
    echo "    ✓ Tailscale installed"
  else
    echo "    ✓ Tailscale already installed"
  fi

  echo ""
  echo "    ── Tailscale Auth ─────────────────────────────────────"
  echo "    To connect this Pi to your Tailscale account, run:"
  echo "      sudo tailscale up"
  echo "    Then authenticate via the URL it prints."
  echo "    Or generate an auth key at tailscale.com/settings/keys"
  echo "    and run: sudo tailscale up --authkey=<key>"
  echo ""
  read -rp "    Do you have a Tailscale auth key to use now? [y/N]: " HAS_TS_KEY
  if [[ "$HAS_TS_KEY" =~ ^[Yy]$ ]]; then
    read -rp "    Paste auth key: " TS_AUTH_KEY
    tailscale up --authkey="$TS_AUTH_KEY" --ssh
    echo "    ✓ Tailscale connected. SSH enabled via Tailscale."
  else
    echo "    Skipping auth — run 'sudo tailscale up --ssh' manually after reboot."
  fi
else
  echo "    Skipped."
fi

# ─── Done ─────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Init complete!                         ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  Next steps:"
echo "  1. Review/edit config: nano $INSTALL_DIR/.env"
echo "  2. If I2C was just enabled: sudo reboot"
echo "  3. After reboot verify I2C: i2cdetect -y 1"
echo "  4. If Tailscale not yet authed: sudo tailscale up --ssh"
echo "  5. Start the system:   sudo bash scripts/start.sh"
echo ""
