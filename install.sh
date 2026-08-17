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

SERVICE_NAME="v4-media-downloader"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

BGUTIL_VERSION="1.3.1"
BGUTIL_DIR="/opt/bgutil-ytdlp-pot-provider"
BGUTIL_SERVER_DIR="${BGUTIL_DIR}/server"

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
    git \
    unzip \
    ffmpeg \
    python3 \
    python3-pip \
    python3-venv

if ! command -v deno >/dev/null 2>&1; then
    log "Installing Deno..."
    TMP_DENO="$(mktemp -d)"

    curl -fsSL https://deno.land/install.sh \
        | DENO_INSTALL="$TMP_DENO/deno" sh -s -- -y

    install -m 0755 "$TMP_DENO/deno/bin/deno" /usr/local/bin/deno
    rm -rf "$TMP_DENO"

    ok "Deno installed."
else
    ok "Deno is already installed."
fi

if [[ ! -f "$ZIP_PATH" ]]; then
    log "Downloading latest release..."
    curl \
        --fail \
        --location \
        --retry 3 \
        --retry-delay 2 \
        --connect-timeout 15 \
        --output "$ZIP_PATH" \
        "$DOWNLOAD_URL"

    [[ -s "$ZIP_PATH" ]] || die "Downloaded package is empty."
    ok "Latest release downloaded."
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

log "Extracting package..."
mkdir -p "$TMP_DIR/app"
unzip -q "$ZIP_PATH" -d "$TMP_DIR/app"

[[ -f "$TMP_DIR/app/app.py" ]] || die "Invalid package: app.py is missing."
[[ -f "$TMP_DIR/app/requirements.txt" ]] || die "Invalid package: requirements.txt is missing."

if systemctl list-unit-files "$SERVICE_NAME.service" >/dev/null 2>&1; then
    log "Stopping existing service..."
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
fi

if [[ -d "$INSTALL_DIR" ]]; then
    BACKUP_DIR="${INSTALL_DIR}.backup-$(date +%Y%m%d-%H%M%S)"
    log "Creating backup: $BACKUP_DIR"
    mv "$INSTALL_DIR" "$BACKUP_DIR"
fi

log "Installing application..."
mkdir -p "$INSTALL_DIR"
cp -a "$TMP_DIR/app/." "$INSTALL_DIR/"

log "Creating application directories..."
install -d -m 0755 \
    "$CONFIG_DIR" \
    "$DATA_DIR" \
    "$DOWNLOAD_DIR" \
    "$THUMB_DIR" \
    "$WORK_DIR"

log "Creating Python environment..."
python3 -m venv "$INSTALL_DIR/.venv"

log "Installing Python dependencies..."
"$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip wheel
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

log "Installing YouTube PO Token provider..."
"$INSTALL_DIR/.venv/bin/pip" install --upgrade \
    "bgutil-ytdlp-pot-provider==$BGUTIL_VERSION"

log "Installing PO Token generator..."
rm -rf "$BGUTIL_DIR"

git clone \
    --quiet \
    --depth 1 \
    --single-branch \
    --branch "$BGUTIL_VERSION" \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
    "$BGUTIL_DIR"

[[ -d "$BGUTIL_SERVER_DIR" ]] || die "PO Token generator installation failed."

log "Installing PO Token generator dependencies..."
(
    cd "$BGUTIL_SERVER_DIR"
    deno install --allow-scripts=npm:canvas --frozen
)

log "Checking YouTube PO Token provider..."

PO_PROVIDER_OUTPUT="$(
    "$INSTALL_DIR/.venv/bin/yt-dlp" \
        -v \
        --skip-download \
        --extractor-args "youtubepot-bgutilscript:server_home=$BGUTIL_SERVER_DIR" \
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
        2>&1 || true
)"

PO_PROVIDER_LINE="$(
    printf '%s\n' "$PO_PROVIDER_OUTPUT" \
        | grep -i "PO Token Providers" \
        | tail -n 1 || true
)"

if printf '%s\n' "$PO_PROVIDER_LINE" | grep -Eqi 'script-deno[^,]*(external\)|available)'; then
    ok "Deno PO Token provider is available."
    log "$PO_PROVIDER_LINE"
else
    warn "Deno PO Token provider is not available."
    warn "Provider status: ${PO_PROVIDER_LINE:-unknown}"
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
    log "Creating configuration..."
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

[[ -f "$COOKIE_FILE" ]] && chmod 600 "$COOKIE_FILE"

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

log "Starting service..."
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
systemctl restart "$SERVICE_NAME"

log "Installing updater helper..."
install -d -m 0755 /usr/local/lib/v4-media-downloader
install -m 0755 "$0" /usr/local/lib/v4-media-downloader/install.sh

sleep 1

if systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "$APP_NAME is running."
else
    warn "Service failed to start. Recent logs:"
    journalctl -u "$SERVICE_NAME" -n 30 --no-pager || true
    die "Installation failed."
fi

[[ -n "$BACKUP_DIR" ]] && log "Previous version backup: $BACKUP_DIR"

ok "Installation completed successfully."

echo
echo "Application:   $INSTALL_DIR"
echo "Configuration: $CONFIG_FILE"
echo "Downloads:     $DOWNLOAD_DIR"
echo "Thumbnails:    $THUMB_DIR"
echo "Work:          $WORK_DIR"
echo "PO Provider:   $BGUTIL_DIR"
echo "Web interface: http://SERVER-IP:${V4MD_PORT:-5000}"
echo "Logs:          journalctl -u $SERVICE_NAME -f"