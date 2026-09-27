# wifi-qr: Headless WiFi provisioning by QR code

**Language:** English | [日本語](README_ja.md)

`wifi-qr` is a small systemd service that lets you join a tiny-llm-node to a WiFi network without a monitor or keyboard. An M5Stack Unit QRCode (I2C) is wired to the Pi 5 GPIO header; when you hold up the WiFi share QR code that your phone generates, the daemon reads it, creates a persistent NetworkManager profile, and connects. Any new QR code switches networks at any time, and the profile survives reboots.

> ⚠️ **Desk design — not yet verified on hardware (2026-09-26).**
> Everything in this directory was written from the vendor documentation and the design spec
> ([docs/superpowers/specs/2026-09-26-wifi-qr-provisioning-design.md](../../docs/superpowers/specs/2026-09-26-wifi-qr-provisioning-design.md)).
> The register map, timing, and current draw have **not** been measured on a real unit.
> Run [RUNBOOK.md](RUNBOOK.md) on the device, then fill in the table below.

### Measured on hardware

| Item | Value |
|---|---|
| Date verified | _(not yet)_ |
| Scanner firmware version (`--probe`) | _(not yet)_ |
| Seconds from scan to `connected` | _(not yet)_ |
| Scanner current draw (5V rail) | _(not measured)_ |
| `i2cdetect -y 1` result | _(not yet)_ |

---

## Hardware

| Item | Detail | Source |
|---|---|---|
| Product | M5Stack Unit QRCode (STM32F030) | [Switch Science 9508](https://www.switch-science.com/products/9508), [M5Stack M5Unit-QRCode (GitHub)](https://github.com/m5stack/M5Unit-QRCode) |
| Interface | I2C, address `0x21` | [I2C protocol sheet](https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/770/UnitQrcode.pdf) |
| Mode switch | Slide switch on the unit. **I2C = "Down"**, UART = "Up" (label from the [schematic](https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/627/SCH_UNIT_QRCODE_V1.0.pdf)) | Schematic |
| Logic level | I2C pull-ups go to **3V3** — connects directly to the Pi GPIO, **no level shifter** | Schematic (R15/R16) |
| Power | 5V over the Grove connector | Schematic |
| Field of view | about ±55° | Product page |

You need one **Grove-to-female-jumper cable** (4 wires). Wire it to the Pi 40-pin header:

| Grove wire | Signal | Pi header pin | GPIO |
|---|---|---|---|
| Red | 5V | pin 2 (or 4) | — |
| Black | GND | pin 6 | — |
| Yellow | SDA | pin 3 | GPIO2 |
| White | SCL | pin 5 | GPIO3 |

**Colours are the M5Stack cable convention; Seeed-brand Grove cables are the reverse (yellow = SCL, white = SDA).** Go by position, not colour: on every Grove connector the four wires sit in the fixed order SCL, SDA, VCC, GND, so the signal wire next to red is SDA (pin 3) and the outermost signal wire, farthest from red, is SCL (pin 5). Swapping the two is electrically harmless (both lines are pulled up to 3V3); `i2cdetect` simply shows nothing until they are the right way round.

Mounting: the field of view is wide (±55°), so the scanner will happily read any QR code that drifts past it. Fix it to the node facing the spot where a user would naturally present a phone (e.g. the front face), not toward a desk or a screen. The unit beeps on every successful decode.

---

## QR code format

The daemon accepts the standard WiFi share format (ZXing / Android / iOS Shortcuts):

```
WIFI:T:WPA;S:MyNetwork;P:secret123;;
WIFI:T:SAE;S:MyWPA3;P:secret123;;
WIFI:T:nopass;S:OpenCafe;;
WIFI:T:WPA;S:Hidden;P:secret123;H:true;;
```

| Field | Meaning |
|---|---|
| `T` | Security: `WPA`, `WPA2`, `WPA3`, `SAE`, `WEP`, `nopass`, or empty (= open). Case-insensitive. `WPA`/`WPA2`/`WPA3` are all applied as `wpa-psk` (`WPA3` = WPA2/WPA3 transition mode); use `SAE` for a WPA3-only AP |
| `S` | SSID (required) |
| `P` | Password. Required for WPA/SAE/WEP; 8–63 characters (or 64 hex digits) for WPA. SAE and WEP lengths are not checked |
| `H` | `true` for a hidden SSID |

Escaping: inside `S` and `P`, the characters `;` `:` `,` `\` must be written as `\;` `\:` `\,` `\\`.
`WPA-EAP` (enterprise) and the `E:` `A:` `I:` `PH2:` keys are rejected as unsupported, and so is any control character (NUL, newline, ...) inside `S` or `P`.

**Android** emits exactly this format from *WiFi settings → Share*. On **iOS**, use a Shortcut or any QR generator. On a Mac or Linux terminal:

```bash
# brew install qrencode   /   sudo apt install qrencode
qrencode -t ANSIUTF8 'WIFI:T:WPA;S:MyNet;P:pass1234;;'
```

---

## How it works

1. The daemon polls the scanner's `READY` register over I2C every 200 ms (`WIFI_QR_POLL_INTERVAL`).
2. When data is ready (`READY` = 1, or 2 when two decodes arrived between polls; both are read the same way) it reads the payload (length register, then the whole payload in one read of the data register — the firmware ignores address offsets inside the data window, so chunked reads would corrupt anything longer than one chunk), clears `READY`, and parses the `WIFI:` string.
3. It applies the credential with `nmcli` as a **persistent NetworkManager profile named `wifi-qr-<ssid>`**. An existing profile with the same name is replaced, so re-scanning after a password change just works.
4. `nmcli connection up` is waited on for up to 30 s. On success the daemon logs the SSID and the interface addresses; on failure it removes the profile.

Scanning is **always on**: any new QR switches the node to that network. If you scan the same credential the daemon last applied while it is still connected to that SSID, it is logged as `already connected` and ignored (after a service restart the first scan is always applied).

---

## CLI

```bash
python3 -m wifi_qr             # run as daemon (what the systemd unit does)
python3 -m wifi_qr --probe     # print I2C response, firmware version, trigger mode, then exit
python3 -m wifi_qr --dry-run   # parse and log (masked) but never call nmcli
python3 -m wifi_qr --once      # exit after applying one credential
python3 -m wifi_qr --verbose   # DEBUG logging
```

Flags can be combined (`--dry-run --once` is the usual test). On an installed device run it as `sudo PYTHONPATH=/opt/wifi-qr python3 -m wifi_qr ...` and stop the service first so the I2C bus is free. `--probe` uses the trigger-mode read as the liveness check; the firmware-version register address is unverified, so if only that read fails it prints `firmware version: unreadable (...)` and still exits 0 (the daemon likewise logs `firmware=unknown` and starts normally).

### Configuration (`/etc/default/wifi-qr`)

The systemd unit reads these environment variables from `/etc/default/wifi-qr`. The same variables work on the command line.

| Variable | Default | Meaning |
|---|---|---|
| `WIFI_QR_I2C_BUS` | `1` | I2C bus number (`/dev/i2c-1` on Pi 5) |
| `WIFI_QR_I2C_ADDR` | `0x21` | Scanner I2C address |
| `WIFI_QR_IFACE` | `wlan0` | WiFi interface handed to nmcli |
| `WIFI_QR_POLL_INTERVAL` | `0.2` | Seconds between `READY` polls |
| `WIFI_QR_FAKE_SCANNER` | _(unset)_ | If set to a `WIFI:...` payload, use an in-memory fake scanner instead of I2C (for testing without hardware) |

---

## Install

On the Pi (Raspberry Pi OS Trixie, set up per [docs/software_setup.md](../../docs/software_setup.md)):

```bash
cd ~/tiny-llm-node/services/wifi-qr
sudo ./install.sh
```

The installer is idempotent. It installs `python3-smbus2` and `i2c-tools`, enables I2C in `/boot/firmware/config.txt`, copies the package to `/opt/wifi-qr/`, creates `/etc/default/wifi-qr` if missing, and enables + starts `wifi-qr.service`. On a Pi 5 `raspi-config` applies the I2C overlay at runtime, so even a first-time enable normally ends with the service started and exit code 0. **Only if `/dev/i2c-1` still does not exist after enabling I2C does it print `REBOOT REQUIRED:` and exit with code 3** — reboot, then continue.

```bash
journalctl -u wifi-qr -f       # watch the log
systemctl status wifi-qr
```

The full step-by-step device procedure, with expected outputs and failure branches, is in **[RUNBOOK.md](RUNBOOK.md)** (Japanese, written to be executed by Claude Code over SSH).

### Uninstall

`sudo ./install.sh --uninstall` stops and disables the service and removes `/opt/wifi-qr` (it keeps `/etc/default/wifi-qr`, the NetworkManager profiles and the apt packages). By hand:

```bash
sudo systemctl disable --now wifi-qr
sudo rm -f /etc/systemd/system/wifi-qr.service /etc/default/wifi-qr
sudo rm -rf /opt/wifi-qr
sudo systemctl daemon-reload
# optional: remove the profiles the daemon created
nmcli -t -f UUID,NAME connection show | awk -F: '$2 ~ /^wifi-qr-/ {print $1}' \
  | xargs -r -n1 sudo nmcli connection delete uuid
```

---

## Security and operational notes

- **Physical access = network control.** Scanning is always on, so anyone who can put a QR code in front of the scanner can change which network the node joins, even while it is connected. This design trusts whoever has physical access to the node.
- Credentials are stored only in NetworkManager keyfiles (`/etc/NetworkManager/system-connections/`, mode 0600).
- The password is **never written to the log**: `WifiCredential` masks it in `repr()`/`str()` and nmcli failure output is summarised, not echoed. It is passed to `nmcli` as an argument, so it is briefly visible in `ps`/`/proc` on the device. This is accepted because the node is a root-operated single-user machine.
- **WiFi country code must be set.** On Raspberry Pi OS, an unset country leaves `wlan0` rfkill-blocked and every connect fails silently. The runbook makes `raspi-config nonint do_wifi_country <CC>` mandatory.
- The scanner keeps its camera running continuously; its red aiming line is driven by the scan engine and may stay off while idle, so do not treat it as a power indicator. Current draw on the 5V rail is expected in the tens to low hundreds of mA but is **unmeasured**; see [docs/power.md](../../docs/power.md).

---

## Future work: status LED

No LED or other user-visible feedback is implemented in this service today. The daemon only exposes a hook for it:

- `wifi_qr.daemon.Feedback` — a Protocol with `on_event(event: FeedbackEvent)`.
- `FeedbackEvent` — `SCANNER_READY`, `SCAN_RECEIVED`, `APPLYING`, `CONNECTED`, `FAILED`, `SCANNER_ERROR`.
- The default is `NullFeedback`, which does nothing.

When the node-wide status LED is implemented, add a class that implements `Feedback` and swap it in for `NullFeedback` in `__main__.py`. Suggested mapping (from spec section 10): waiting → steady, scan received → short blink, applying → blink, connected → on for a while then waiting, failed → fast blink, scanner error → distinct pattern. The daemon does not depend on how the LED is driven (GPIO, Pi ACT LED, I2C driver, ...).

---

## Development

Unit tests run on any machine; no hardware or `smbus2` is needed (it is imported lazily).

```bash
cd services/wifi-qr
python3 -m venv .venv && . .venv/bin/activate
pip install pytest smbus2
python -m pytest -q
```

or with [uv](https://docs.astral.sh/uv/): `uv run --with pytest --with smbus2 python -m pytest -q`.

Try the CLI without hardware:

```bash
WIFI_QR_FAKE_SCANNER='WIFI:T:nopass;S:wifi-qr-test;;' python -m wifi_qr --dry-run --once
```
