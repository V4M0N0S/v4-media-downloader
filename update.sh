#!/usr/bin/env bash
set -Eeuo pipefail

REPO="V4M0N0S/v4-media-downloader"
ASSET_NAME="latest.zip"
DOWNLOAD_URL="https://github.com/${REPO}/releases/latest/download/${ASSET_NAME}"
INSTALLER_LOCAL="/usr/local/lib/v4-media-downloader/install.sh"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

log()  { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[ OK ]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Please run this updater as root."

if ! command -v curl >/dev/null 2>&1; then
    log "Installing curl..."
    apt-get update
    apt-get install -y ca-certificates curl
fi

if ! command -v unzip >/dev/null 2>&1; then
    log "Installing unzip..."
    apt-get update
    apt-get install -y unzip
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
ZIP_PATH="$TMP_DIR/$ASSET_NAME"

log "Downloading latest release from GitHub..."
log "$DOWNLOAD_URL"

curl \
    --fail \
    --location \
    --retry 3 \
    --retry-delay 2 \
    --connect-timeout 15 \
    --output "$ZIP_PATH" \
    "$DOWNLOAD_URL"

[[ -s "$ZIP_PATH" ]] || die "Downloaded file is empty."

log "Validating downloaded package..."
unzip -tq "$ZIP_PATH" >/dev/null || die "Downloaded ZIP file is corrupted."

if [[ -x "$INSTALLER_LOCAL" ]]; then
    INSTALLER="$INSTALLER_LOCAL"
elif [[ -x "$SCRIPT_DIR/install.sh" ]]; then
    INSTALLER="$SCRIPT_DIR/install.sh"
elif [[ -x "$SCRIPT_DIR/install-v56-en.sh" ]]; then
    INSTALLER="$SCRIPT_DIR/install-v56-en.sh"
else
    die "install.sh was not found. Place install.sh next to update.sh or run install.sh first."
fi

log "Installing update..."
"$INSTALLER" "$ZIP_PATH"

ok "Update completed successfully."
