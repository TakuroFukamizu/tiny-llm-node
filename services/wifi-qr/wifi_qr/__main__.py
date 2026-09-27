"""Command line entry point: ``python3 -m wifi_qr``.

Without flags the process runs as the provisioning daemon (this is what the
systemd unit starts). Flags:

``--probe``
    Read the trigger mode (the liveness check) and the firmware version from
    the unit and print three lines (``i2c: bus=1 addr=0x21 ok`` /
    ``firmware version: 0x..`` / ``trigger mode: auto``), exit 0; exit 2
    with an ``i2c: ... error`` line and a wiring hint if the I2C bus does
    not answer. The firmware-version register address is unverified (spec
    section 2), so a failure there alone prints
    ``firmware version: unreadable (...)`` and still exits 0.
``--dry-run``
    Parse and log credentials (password masked) without calling nmcli.
``--once``
    Exit after the first credential has been applied (0 = connected).
``--verbose``
    DEBUG logging.

Configuration comes from the environment (``/etc/default/wifi-qr`` via the
unit's ``EnvironmentFile``):

==========================  =======  ==========================================
Variable                    Default  Meaning
==========================  =======  ==========================================
``WIFI_QR_I2C_BUS``         1        ``/dev/i2c-<n>``
``WIFI_QR_I2C_ADDR``        0x21     unit address, hex or decimal
``WIFI_QR_IFACE``           wlan0    interface the NetworkManager profile binds
``WIFI_QR_POLL_INTERVAL``   0.2      seconds between READY polls
``WIFI_QR_FAKE_SCANNER``    (unset)  if set, a ``FakeScanner`` serving this one
                                     payload replaces the I2C driver (tests,
                                     runbook step without hardware)
==========================  =======  ==========================================

Logs go to stderr as ``LEVEL logger: message`` (journald adds timestamps).
The daemon is wired with :class:`~wifi_qr.daemon.NullFeedback`; the future
status LED replaces it here (spec section 10).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Mapping, Sequence

from wifi_qr.daemon import Daemon, NullFeedback
from wifi_qr.network import DryRunBackend, NetworkBackend, NetworkManagerBackend
from wifi_qr.scanner import (
    TRIGGER_MODE_AUTO,
    TRIGGER_MODE_MANUAL,
    FakeScanner,
    Scanner,
    ScannerError,
    UnitQRCode,
)

LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

_PROBE_HELP = (
    "Could not talk to the QR scanner over I2C.\n"
    "Check: (1) `i2cdetect -y {bus}` shows the unit at 0x{addr:02x};\n"
    "       (2) the wiring: red->pin 2 (5V), black->pin 6 (GND), "
    "SDA->pin 3 (GPIO2), SCL->pin 5 (GPIO3); SDA is the signal wire next to "
    "red, SCL the outermost\n"
    "           (M5Stack cables: yellow=SDA/white=SCL; Seeed cables are the "
    "reverse -- try swapping the two);\n"
    "       (3) the slide switch on the unit is in the I2C position;\n"
    "       (4) I2C is enabled (dtparam=i2c_arm=on, reboot after enabling)."
)


class ConfigError(ValueError):
    """An environment variable holds a value that cannot be parsed."""


@dataclass(frozen=True)
class Config:
    """Settings read from the environment."""

    i2c_bus: int = 1
    i2c_addr: int = 0x21
    iface: str = "wlan0"
    poll_interval: float = 0.2
    fake_payload: str | None = None


def _number(env: Mapping[str, str], key: str, default, parse) -> int | float:
    value = env.get(key, "").strip()
    if not value:
        return default
    try:
        return parse(value)
    except ValueError as exc:
        raise ConfigError(f"{key}={value!r} is not a valid number") from exc


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Build a :class:`Config` from ``env`` (default ``os.environ``)."""
    env = os.environ if env is None else env
    fake = env.get("WIFI_QR_FAKE_SCANNER", "")
    return Config(
        i2c_bus=int(_number(env, "WIFI_QR_I2C_BUS", 1, lambda v: int(v, 0))),
        i2c_addr=int(_number(env, "WIFI_QR_I2C_ADDR", 0x21, lambda v: int(v, 0))),
        iface=env.get("WIFI_QR_IFACE", "").strip() or "wlan0",
        poll_interval=float(_number(env, "WIFI_QR_POLL_INTERVAL", 0.2, float)),
        fake_payload=fake or None,
    )


def build_scanner(cfg: Config) -> Scanner:
    """``FakeScanner`` when ``WIFI_QR_FAKE_SCANNER`` is set, else the I2C driver.

    Opening the real bus may raise ``OSError`` (missing ``/dev/i2c-N``).
    """
    if cfg.fake_payload is not None:
        return FakeScanner([cfg.fake_payload.encode("utf-8")])
    return UnitQRCode(bus=cfg.i2c_bus, addr=cfg.i2c_addr)


def build_backend(cfg: Config, dry_run: bool = False) -> NetworkBackend:
    """``DryRunBackend`` for ``--dry-run``, else nmcli on ``cfg.iface``."""
    if dry_run:
        return DryRunBackend()
    return NetworkManagerBackend(iface=cfg.iface)


def probe(scanner: Scanner, cfg: Config | None = None) -> int:
    """Print I2C status, firmware version and trigger mode; never writes to the unit.

    Output (stdout, three lines, matched by RUNBOOK.md step 5)::

        i2c: bus=1 addr=0x21 ok
        firmware version: 0x05          (or "unreadable (<error>)")
        trigger mode: auto

    The trigger-mode read decides whether the bus answers; the firmware
    version is diagnostic only (its register address is unverified) and can
    never turn the probe into a failure.
    """
    cfg = cfg or Config()
    try:
        get_mode = getattr(scanner, "get_trigger_mode", None)
        mode = get_mode() if callable(get_mode) else TRIGGER_MODE_AUTO
    except OSError as exc:
        return _probe_failed(exc, cfg)
    try:
        firmware = f"0x{scanner.firmware_version():02x}"
    except (OSError, ScannerError) as exc:
        firmware = f"unreadable ({exc})"
    print(f"i2c: bus={cfg.i2c_bus} addr=0x{cfg.i2c_addr:02x} ok")
    print(f"firmware version: {firmware}")
    print(f"trigger mode: {_trigger_mode_name(mode)}")
    return EXIT_OK


def _trigger_mode_name(mode: int) -> str:
    if mode == TRIGGER_MODE_AUTO:
        return "auto"
    if mode == TRIGGER_MODE_MANUAL:
        return "manual"
    return f"unknown ({mode})"


def _probe_failed(exc: OSError, cfg: Config) -> int:
    print(f"i2c: bus={cfg.i2c_bus} addr=0x{cfg.i2c_addr:02x} error: {exc}", file=sys.stderr)
    print(_PROBE_HELP.format(bus=cfg.i2c_bus, addr=cfg.i2c_addr), file=sys.stderr)
    return EXIT_USAGE


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wifi_qr",
        description="Headless WiFi provisioning from WIFI: QR codes (M5Stack Unit QRCode over I2C).",
    )
    parser.add_argument("--probe", action="store_true", help="show scanner status and exit")
    parser.add_argument("--dry-run", action="store_true", help="log parsed credentials, do not call nmcli")
    parser.add_argument("--once", action="store_true", help="exit after the first credential is applied")
    parser.add_argument("--verbose", action="store_true", help="DEBUG logging")
    return parser.parse_args(argv)


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(stream=sys.stderr, format=LOG_FORMAT, level=level)
    # basicConfig is a no-op once the root logger has handlers (pytest, embedding);
    # set our package level explicitly so --verbose always takes effect.
    logging.getLogger("wifi_qr").setLevel(level)


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """Run the CLI; returns the process exit code (never calls ``sys.exit``)."""
    args = parse_args(argv)
    configure_logging(args.verbose)
    try:
        cfg = load_config(env)
    except ConfigError as exc:
        print(f"wifi_qr: {exc}", file=sys.stderr)
        return EXIT_USAGE

    try:
        scanner = build_scanner(cfg)
    except OSError as exc:
        if args.probe:
            return _probe_failed(exc, cfg)
        logging.getLogger("wifi_qr").error("cannot open I2C bus %d: %s", cfg.i2c_bus, exc)
        return EXIT_FAILURE

    if args.probe:
        return probe(scanner, cfg)

    daemon = Daemon(
        scanner,
        build_backend(cfg, dry_run=args.dry_run),
        feedback=NullFeedback(),
        poll_interval=cfg.poll_interval,
        sleep=time.sleep,
        once=args.once,
    )
    return daemon.run()


if __name__ == "__main__":
    raise SystemExit(main())
