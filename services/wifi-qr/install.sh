#!/usr/bin/env bash
# install.sh - idempotent installer for the wifi-qr daemon on Raspberry Pi OS.
#
# Usage:
#   sudo ./install.sh              install or upgrade (safe to re-run)
#   sudo ./install.sh --uninstall  stop/disable the service and remove /opt/wifi-qr
#   ./install.sh --help
#
# What it does (see docs/superpowers/specs/2026-09-26-wifi-qr-provisioning-design.md):
#   1. apt-get install python3-smbus2 i2c-tools (only if missing)
#   2. enable the I2C bus via raspi-config (dtparam=i2c_arm=on)
#   3. copy wifi_qr/ to /opt/wifi-qr/wifi_qr/
#   4. create /etc/default/wifi-qr from wifi-qr.env.example if absent
#   5. install + enable wifi-qr.service, and start it if /dev/i2c-1 exists
#
# Exit codes:
#   0  installed and running (or uninstalled)
#   1  error
#   3  installed, but a REBOOT IS REQUIRED before /dev/i2c-1 appears;
#      the service is enabled and will start automatically after the reboot.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELF="${SCRIPT_DIR}/$(basename "${BASH_SOURCE[0]}")"
cd "$SCRIPT_DIR"

readonly INSTALL_DIR=/opt/wifi-qr
readonly ENV_FILE=/etc/default/wifi-qr
readonly UNIT_NAME=wifi-qr.service
readonly UNIT_DST="/etc/systemd/system/${UNIT_NAME}"
readonly I2C_DEV=/dev/i2c-1
readonly APT_PACKAGES=(python3-smbus2 i2c-tools)
readonly EXIT_REBOOT_REQUIRED=3

# Set by enable_i2c(): whether config.txt had I2C on before this run, and
# whether this run turned it on. Used to decide between "start" and "reboot".
I2C_WAS_ENABLED=unknown # yes | no | unknown (raspi-config not available)
I2C_JUST_ENABLED=no     # yes | no

log() { printf '[wifi-qr] %s\n' "$*"; }
warn() { printf '[wifi-qr] WARNING: %s\n' "$*" >&2; }
die() {
  printf '[wifi-qr] ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  sed -n '2,/^set -euo/p' "${SELF}" | sed '$d' | sed 's/^# \{0,1\}//'
}

require_root() {
  if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    die "this script must be run as root: sudo $0${*:+ $*}"
  fi
}

# ---------------------------------------------------------------------------
# 1. apt packages
# ---------------------------------------------------------------------------
package_installed() {
  local status
  status="$(dpkg-query -W -f='${Status}' "$1" 2>/dev/null || true)"
  [[ $status == "install ok installed" ]]
}

install_packages() {
  local missing=()
  local pkg
  for pkg in "${APT_PACKAGES[@]}"; do
    package_installed "$pkg" || missing+=("$pkg")
  done
  if [[ ${#missing[@]} -eq 0 ]]; then
    log "apt packages already installed: ${APT_PACKAGES[*]}"
    return
  fi
  log "installing apt packages: ${missing[*]}"
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}"
}

# ---------------------------------------------------------------------------
# 2. I2C bus
# ---------------------------------------------------------------------------
enable_i2c() {
  if ! command -v raspi-config >/dev/null 2>&1; then
    warn "raspi-config not found; cannot enable I2C automatically (not Raspberry Pi OS?)"
    return
  fi
  # `raspi-config nonint get_i2c` prints 0 when dtparam=i2c_arm=on is set.
  local state
  state="$(raspi-config nonint get_i2c 2>/dev/null || echo 1)"
  if [[ $state == "0" ]]; then
    I2C_WAS_ENABLED=yes
    log "I2C already enabled in config.txt"
  else
    I2C_WAS_ENABLED=no
    log "enabling I2C (raspi-config nonint do_i2c 0)"
    raspi-config nonint do_i2c 0
    I2C_JUST_ENABLED=yes
  fi
  # Harmless if already loaded. When the overlay is already active this is
  # enough for /dev/i2c-1 to appear without a reboot.
  modprobe i2c-dev 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# 3. code
# ---------------------------------------------------------------------------
deploy_code() {
  [[ -d wifi_qr ]] || die "wifi_qr/ not found next to install.sh"
  mkdir -p "${INSTALL_DIR}"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' \
      wifi_qr/ "${INSTALL_DIR}/wifi_qr/"
  else
    rm -rf "${INSTALL_DIR}/wifi_qr"
    cp -r wifi_qr "${INSTALL_DIR}/wifi_qr"
  fi
  find "${INSTALL_DIR}" -type d -name __pycache__ -prune -exec rm -rf {} +
  find "${INSTALL_DIR}" -type f -name '*.pyc' -delete
  log "deployed wifi_qr/ to ${INSTALL_DIR}/wifi_qr/"
}

# ---------------------------------------------------------------------------
# 4. config
# ---------------------------------------------------------------------------
install_env_file() {
  if [[ -e ${ENV_FILE} ]]; then
    log "keeping existing ${ENV_FILE}"
  else
    # 0600: WIFI_QR_FAKE_SCANNER may be set to a payload containing a password.
    install -m 0600 wifi-qr.env.example "${ENV_FILE}"
    log "installed default config to ${ENV_FILE}"
  fi
}

# ---------------------------------------------------------------------------
# 5. systemd
# ---------------------------------------------------------------------------
install_unit() {
  install -m 0644 "${UNIT_NAME}" "${UNIT_DST}"
  systemctl daemon-reload
  systemctl enable "${UNIT_NAME}" >/dev/null 2>&1
  log "installed and enabled ${UNIT_NAME}"
}

# Prints the closing summary. $1 = "started" | "reboot" | "no-i2c"
print_summary() {
  local outcome=$1
  echo
  echo "wifi-qr install summary"
  echo "  code:    ${INSTALL_DIR}/wifi_qr/"
  echo "  config:  ${ENV_FILE}"
  echo "  unit:    ${UNIT_DST}"
  case "${outcome}" in
    started)
      echo "  status:  running"
      echo
      echo "Next steps:"
      echo "  systemctl status wifi-qr --no-pager"
      echo "  journalctl -u wifi-qr -f        # then hold a WiFi QR code up to the scanner"
      ;;
    reboot)
      echo "  status:  enabled, NOT started (${I2C_DEV} does not exist yet)"
      echo
      echo "REBOOT REQUIRED: I2C was enabled in /boot/firmware/config.txt but the"
      echo "bus will only appear after a reboot. The service starts automatically on boot."
      echo "  sudo reboot"
      echo "After the reboot:"
      echo "  i2cdetect -y 1                  # expect '21' in the table"
      echo "  journalctl -u wifi-qr -f"
      ;;
    no-i2c)
      echo "  status:  enabled, NOT started (${I2C_DEV} does not exist)"
      echo
      echo "Enable the I2C bus manually (dtparam=i2c_arm=on, modprobe i2c-dev), then"
      echo "  sudo systemctl start wifi-qr"
      ;;
  esac
}

start_or_defer() {
  if [[ -e ${I2C_DEV} ]]; then
    systemctl restart "${UNIT_NAME}"
    log "service restarted"
    print_summary started
    return 0
  fi

  # No bus device. If config.txt has I2C on (whether from this run or an
  # earlier one that was never followed by a reboot), a reboot will fix it.
  if [[ ${I2C_JUST_ENABLED} == yes || ${I2C_WAS_ENABLED} == yes ]]; then
    print_summary reboot
    return "${EXIT_REBOOT_REQUIRED}"
  fi

  warn "${I2C_DEV} not found and I2C could not be enabled automatically"
  print_summary no-i2c
  return 1
}

install_all() {
  install_packages
  enable_i2c
  deploy_code
  install_env_file
  install_unit
  start_or_defer
}

# ---------------------------------------------------------------------------
# --uninstall
# ---------------------------------------------------------------------------
uninstall_all() {
  if [[ -e ${UNIT_DST} ]]; then
    systemctl disable --now "${UNIT_NAME}" >/dev/null 2>&1 || true
    systemctl reset-failed "${UNIT_NAME}" >/dev/null 2>&1 || true
    rm -f "${UNIT_DST}"
    systemctl daemon-reload
    log "removed ${UNIT_DST}"
  else
    log "${UNIT_DST} not present"
  fi
  rm -rf "${INSTALL_DIR}"
  log "removed ${INSTALL_DIR}"
  echo
  echo "Kept on purpose:"
  echo "  ${ENV_FILE}"
  echo "  NetworkManager profiles wifi-qr-<ssid> (nmcli connection show | grep wifi-qr-)"
  echo "  apt packages python3-smbus2 i2c-tools, and the I2C setting in config.txt"
}

main() {
  local mode=install
  local arg
  for arg in "$@"; do
    case "${arg}" in
      --uninstall) mode=uninstall ;;
      -h | --help)
        usage
        exit 0
        ;;
      *) die "unknown option: ${arg} (try --help)" ;;
    esac
  done

  require_root "$@"
  case "${mode}" in
    install) install_all ;;
    uninstall) uninstall_all ;;
  esac
}

main "$@"
