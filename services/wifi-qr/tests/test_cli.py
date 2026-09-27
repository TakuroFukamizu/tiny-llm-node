"""Tests for the ``python -m wifi_qr`` command line (wifi_qr.__main__)."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import pytest

from wifi_qr import __main__ as cli
from wifi_qr.network import DryRunBackend, NetworkManagerBackend
from wifi_qr.scanner import FakeScanner

PW = "s3cretPassw0rd"
PAYLOAD = f"WIFI:T:WPA;S:Cafe;P:{PW};;"


@pytest.fixture(autouse=True)
def _reset_package_logger_level():
    """``main()`` pins the ``wifi_qr`` logger level; undo it after each test."""
    yield
    logging.getLogger("wifi_qr").setLevel(logging.NOTSET)


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #


def test_defaults_when_env_empty():
    cfg = cli.load_config({})
    assert cfg.i2c_bus == 1
    assert cfg.i2c_addr == 0x21
    assert cfg.iface == "wlan0"
    assert cfg.poll_interval == pytest.approx(0.2)
    assert cfg.fake_payload is None


@pytest.mark.parametrize("value,expected", [("0x21", 0x21), ("0X21", 0x21), ("33", 33), ("0x3f", 0x3F)])
def test_addr_accepts_hex_and_decimal(value, expected):
    cfg = cli.load_config({"WIFI_QR_I2C_ADDR": value})
    assert cfg.i2c_addr == expected


def test_env_overrides():
    cfg = cli.load_config(
        {
            "WIFI_QR_I2C_BUS": "3",
            "WIFI_QR_IFACE": "wlan1",
            "WIFI_QR_POLL_INTERVAL": "0.5",
            "WIFI_QR_FAKE_SCANNER": PAYLOAD,
        }
    )
    assert cfg.i2c_bus == 3
    assert cfg.iface == "wlan1"
    assert cfg.poll_interval == pytest.approx(0.5)
    assert cfg.fake_payload == PAYLOAD


def test_empty_fake_scanner_is_unset():
    assert cli.load_config({"WIFI_QR_FAKE_SCANNER": ""}).fake_payload is None


@pytest.mark.parametrize("key", ["WIFI_QR_I2C_BUS", "WIFI_QR_I2C_ADDR", "WIFI_QR_POLL_INTERVAL"])
def test_invalid_numeric_env_raises_config_error(key):
    with pytest.raises(cli.ConfigError) as info:
        cli.load_config({key: "banana"})
    assert key in str(info.value)


def test_main_reports_config_error(capsys):
    rc = cli.main([], env={"WIFI_QR_I2C_BUS": "x"})
    assert rc == 2
    assert "WIFI_QR_I2C_BUS" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #


def test_build_scanner_fake_when_env_set():
    cfg = cli.load_config({"WIFI_QR_FAKE_SCANNER": PAYLOAD})
    scanner = cli.build_scanner(cfg)
    assert isinstance(scanner, FakeScanner)
    assert scanner.payloads == [PAYLOAD.encode()]


def test_build_scanner_real_uses_config(monkeypatch):
    calls = {}

    class StubUnit:
        def __init__(self, bus, addr):
            calls.update(bus=bus, addr=addr)

    monkeypatch.setattr(cli, "UnitQRCode", StubUnit)
    cfg = cli.load_config({"WIFI_QR_I2C_BUS": "2", "WIFI_QR_I2C_ADDR": "0x22"})
    scanner = cli.build_scanner(cfg)
    assert isinstance(scanner, StubUnit)
    assert calls == {"bus": 2, "addr": 0x22}


def test_build_backend_dry_run_and_real():
    cfg = cli.load_config({"WIFI_QR_IFACE": "wlan9"})
    assert isinstance(cli.build_backend(cfg, dry_run=True), DryRunBackend)
    real = cli.build_backend(cfg, dry_run=False)
    assert isinstance(real, NetworkManagerBackend)
    assert real._iface == "wlan9"


# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #


def test_parse_args_flags():
    args = cli.parse_args(["--probe", "--dry-run", "--once", "--verbose"])
    assert args.probe and args.dry_run and args.once and args.verbose
    args = cli.parse_args([])
    assert not (args.probe or args.dry_run or args.once or args.verbose)


# --------------------------------------------------------------------------- #
# --dry-run --once
# --------------------------------------------------------------------------- #


def test_dry_run_once_with_fake_scanner(caplog):
    caplog.set_level(logging.DEBUG)
    rc = cli.main(["--dry-run", "--once", "--verbose"], env={"WIFI_QR_FAKE_SCANNER": PAYLOAD})
    assert rc == 0
    assert "WifiCredential(ssid='Cafe', password=***, security=wpa-psk, hidden=False)" in caplog.text
    assert "dry-run: would apply ssid='Cafe' security=wpa-psk" in caplog.text
    assert PW not in caplog.text
    for record in caplog.records:
        assert PW not in record.getMessage()
        assert PW not in repr(record.args)


def test_dry_run_once_open_network(caplog):
    caplog.set_level(logging.INFO)
    rc = cli.main(["--dry-run", "--once"], env={"WIFI_QR_FAKE_SCANNER": "WIFI:T:nopass;S:wifi-qr-test;;"})
    assert rc == 0
    # The lines RUNBOOK.md step 6a expects (password=None: an open network has no secret).
    assert (
        "scan received: WifiCredential(ssid='wifi-qr-test', password=None, security=open, hidden=False)"
        in caplog.text
    )
    assert "dry-run: would apply ssid='wifi-qr-test' security=open" in caplog.text


def test_dry_run_once_bad_payload_exits_1(caplog):
    # --once stops after the first processed payload; a rejected one is exit 1.
    caplog.set_level(logging.INFO)
    rc = cli.main(["--dry-run", "--once"], env={"WIFI_QR_FAKE_SCANNER": "not a wifi code"})
    assert rc == 1
    assert "not a wifi code" not in caplog.text
    assert "PayloadError" in caplog.text


def test_verbose_sets_debug_level(caplog):
    caplog.set_level(logging.DEBUG)
    cli.main(["--dry-run", "--once", "--verbose"], env={"WIFI_QR_FAKE_SCANNER": PAYLOAD})
    assert logging.getLogger("wifi_qr").getEffectiveLevel() == logging.DEBUG
    cli.main(["--dry-run", "--once"], env={"WIFI_QR_FAKE_SCANNER": PAYLOAD})
    assert logging.getLogger("wifi_qr").getEffectiveLevel() == logging.INFO


# --------------------------------------------------------------------------- #
# --probe
# --------------------------------------------------------------------------- #


def test_probe_with_fake_scanner(capsys):
    rc = cli.main(["--probe"], env={"WIFI_QR_FAKE_SCANNER": PAYLOAD})
    assert rc == 0
    out = capsys.readouterr().out
    assert out == "i2c: bus=1 addr=0x21 ok\nfirmware version: 0x00\ntrigger mode: auto\n"


def test_probe_uses_get_trigger_mode_when_available(capsys):
    class ProbeScanner(FakeScanner):
        def firmware_version(self) -> int:
            return 7

        def get_trigger_mode(self) -> int:
            return 1

    rc = cli.probe(ProbeScanner())
    assert rc == 0
    assert capsys.readouterr().out == "i2c: bus=1 addr=0x21 ok\nfirmware version: 0x07\ntrigger mode: manual\n"


def test_probe_does_not_write_to_scanner(capsys):
    scanner = FakeScanner()
    cli.probe(scanner)
    assert scanner.trigger_mode_calls == []
    assert scanner.cleared == 0


def test_probe_oserror_prints_help_and_exits_2(capsys):
    class DeadScanner(FakeScanner):
        # The trigger-mode read is the liveness check; the unit answers nothing.
        def get_trigger_mode(self) -> int:
            raise OSError(121, "Remote I/O error")

        def firmware_version(self) -> int:
            raise OSError(121, "Remote I/O error")

    rc = cli.probe(DeadScanner())
    assert rc == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "i2c: bus=1 addr=0x21 error: [Errno 121] Remote I/O error" in err
    for hint in ("i2cdetect", "wiring", "switch"):
        assert hint in err


def test_probe_unreadable_firmware_version_is_not_an_error(capsys):
    # Spec section 2: the FW-version register (0x00FE vs 0x00F0) is diagnostic
    # only. A NACK there must not send the runbook down the wiring branch.
    class NoFwScanner(FakeScanner):
        def get_trigger_mode(self) -> int:
            return 0

        def firmware_version(self) -> int:
            raise OSError(121, "Remote I/O error")

    rc = cli.probe(NoFwScanner())
    assert rc == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert out == (
        "i2c: bus=1 addr=0x21 ok\n"
        "firmware version: unreadable ([Errno 121] Remote I/O error)\n"
        "trigger mode: auto\n"
    )


def test_probe_oserror_from_build_scanner_exits_2(monkeypatch, capsys):
    def broken(_cfg):
        raise FileNotFoundError(2, "No such file or directory", "/dev/i2c-1")

    monkeypatch.setattr(cli, "build_scanner", broken)
    rc = cli.main(["--probe"], env={})
    assert rc == 2
    assert "/dev/i2c-1" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# module entry point
# --------------------------------------------------------------------------- #


def test_python_dash_m_entry_point():
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "-m", "wifi_qr", "--dry-run", "--once"],
        cwd=root,
        env={"PATH": "/usr/bin:/bin", "WIFI_QR_FAKE_SCANNER": PAYLOAD},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "INFO wifi_qr.network: dry-run: would apply ssid='Cafe' security=wpa-psk" in proc.stderr
    assert PW not in proc.stderr
    assert PW not in proc.stdout
