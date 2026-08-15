#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="V4 Media Downloader"
REPO="V4M0N0S/v4-media-downloader"
ASSET_NAME="latest.zip"
DOWNLOAD_URL="https://github.com/${REPO}/releases/latest/download/${ASSET_NAME}"

INSTALL_DIR="${V4MD_INSTALL_DIR:-/opt/v4-media-downloader}"
CONFIG_DIR="${V4MD_CONFIG_DIR:-/etc/v4-media-downloader}"
CONFIG_FILE="${V4MD_CONFIG_FILE:-$CONFIG_DIR/config.env}"
DATA_DIR="${V4MD_DATA_DIR:-/var/lib/v4-media-downloader}"
DOWNLOAD_DIR="${V4MD_DOWNLOAD_DIR:-$DATA_DIR/downloads}"
THUMB_DIR="${V4MD_THUMB_DIR:-$DATA_DIR/thumbnails}"
WORK_DIR="${V4MD_WORK_DIR:-$DATA_DIR/work}"
COOKIE_FILE="${V4MD_COOKIE_FILE:-$CONFIG_DIR/youtube-cookies.txt}"

SERVICE_FILE="/etc/systemd/system/v4-media-downloader.service"
SERVICE_NAME="v4-media-downloader"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ZIP_PATH="${1:-${V4MD_ZIP_PATH:-$SCRIPT_DIR/latest.zip}}"

log()  { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[ OK ]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Please run this installer as root."

export DEBIAN_FRONTEND=noninteractive

log "Installing system dependencies..."
apt-get update
apt-get install -y \
    ca-certificates \
    curl \
    unzip \
    ffmpeg \
    python3 \
    python3-pip \
    python3-venv

if ! command -v deno >/dev/null 2>&1; then
    log "Installing Deno..."
    tmp_deno="$(mktemp -d)"
    curl -fsSL https://deno.land/install.sh | DENO_INSTALL="$tmp_deno/deno" sh
    install -m 0755 "$tmp_deno/deno/bin/deno" /usr/local/bin/deno
    rm -rf "$tmp_deno"
else
    ok "Deno is already installed."
fi

if [[ ! -f "$ZIP_PATH" ]]; then
    log "latest.zip was not found locally."
    log "Downloading the latest release from GitHub..."
    log "$DOWNLOAD_URL"

    curl \
        --fail \
        --location \
        --retry 3 \
        --retry-delay 2 \
        --connect-timeout 15 \
        --output "$ZIP_PATH" \
        "$DOWNLOAD_URL"

    [[ -s "$ZIP_PATH" ]] || die "Downloaded package is empty."
    ok "Latest release downloaded successfully."
else
    ok "Using local package: $ZIP_PATH"
fi

TMP_DIR="$(mktemp -d)"
BACKUP_DIR=""

cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

log "Validating package..."
unzip -tq "$ZIP_PATH" >/dev/null || die "ZIP file is corrupted."

log "Extracting $(basename "$ZIP_PATH")..."
mkdir -p "$TMP_DIR/app"
unzip -q "$ZIP_PATH" -d "$TMP_DIR/app"

[[ -f "$TMP_DIR/app/app.py" ]] || die "Invalid package: app.py is missing."
[[ -f "$TMP_DIR/app/requirements.txt" ]] || die "Invalid package: requirements.txt is missing."

if systemctl list-unit-files "$SERVICE_NAME.service" >/dev/null 2>&1; then
    log "Stopping existing service..."
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
fi

if [[ -d "$INSTALL_DIR" ]]; then
    stamp="$(date +%Y%m%d-%H%M%S)"
    BACKUP_DIR="${INSTALL_DIR}.backup-${stamp}"
    log "Backing up existing installation to $BACKUP_DIR"
    mv "$INSTALL_DIR" "$BACKUP_DIR"
fi

log "Installing application files..."
mkdir -p "$INSTALL_DIR"
cp -a "$TMP_DIR/app/." "$INSTALL_DIR/"

log "Creating application directories..."
install -d -m 0755 "$CONFIG_DIR"
install -d -m 0755 "$DATA_DIR"
install -d -m 0755 "$DOWNLOAD_DIR"
install -d -m 0755 "$THUMB_DIR"
install -d -m 0755 "$WORK_DIR"

log "Creating Python virtual environment..."
python3 -m venv "$INSTALL_DIR/.venv"

log "Installing Python dependencies..."
"$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip wheel
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

if [[ ! -f "$CONFIG_FILE" ]]; then
    log "Creating configuration at $CONFIG_FILE"
    cat > "$CONFIG_FILE" <<EOF
DISCORD_WEBHOOK_URL=""
YTDLP_COOKIE_FILE="$COOKIE_FILE"
V4MD_DOWNLOAD_DIR="$DOWNLOAD_DIR"
V4MD_THUMB_DIR="$THUMB_DIR"
V4MD_WORK_DIR="$WORK_DIR"
V4MD_CONFIG_DIR="$CONFIG_DIR"
V4MD_COOKIE_FILE="$COOKIE_FILE"
V4MD_HOST="0.0.0.0"
V4MD_PORT="5000"
EOF
    chmod 600 "$CONFIG_FILE"
else
    ok "Existing configuration will be preserved."
fi

if [[ -f "$COOKIE_FILE" ]]; then
    chmod 600 "$COOKIE_FILE"
fi

log "Creating systemd service..."
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=V4 Media Downloader
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=-$CONFIG_FILE
ExecStart=$INSTALL_DIR/.venv/bin/python $INSTALL_DIR/app.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

log "Enabling and starting service..."
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
systemctl restart "$SERVICE_NAME"

log "Installing local updater helper..."
install -d -m 0755 /usr/local/lib/v4-media-downloader
install -m 0755 "$0" /usr/local/lib/v4-media-downloader/install.sh

sleep 1

if systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "$APP_NAME is running."
else
    warn "Service is not active. Recent logs:"
    journalctl -u "$SERVICE_NAME" -n 30 --no-pager || true
    die "Installation failed."
fi

if [[ -n "$BACKUP_DIR" ]]; then
    log "Previous version backup: $BACKUP_DIR"
fi

ok "Installation completed successfully."
echo
echo "Application directory: $INSTALL_DIR"
echo "Configuration file: $CONFIG_FILE"
echo "Download directory: $DOWNLOAD_DIR"
echo "Thumbnail directory: $THUMB_DIR"
echo "Work directory: $WORK_DIR"
echo "Web interface: http://SERVER-IP:${V4MD_PORT:-5000}"
echo "Logs: journalctl -u $SERVICE_NAME -f"
