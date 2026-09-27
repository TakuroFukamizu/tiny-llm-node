"""Tests for wifi_qr.network (nmcli wrapper) using a fake subprocess runner."""

from __future__ import annotations

import logging
import subprocess
from typing import Any

import pytest

from wifi_qr import network
from wifi_qr.network import (
    ConnectResult,
    DryRunBackend,
    NetworkManagerBackend,
    profile_name,
)
from wifi_qr.payload import Security, WifiCredential

PW = "secret123"
SSID = "Home"
NAME = "wifi-qr-Home"


def _kind(argv: list[str]) -> str:
    """Classify an nmcli argv into show/delete/add/up/device-wifi/device-show."""
    if "connection" in argv:
        return argv[argv.index("connection") + 1]
    if "device" in argv:
        return "device-" + argv[argv.index("device") + 1]
    return "unknown"


class FakeRunner:
    """Records every invocation and returns configured results per command kind."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []
        self.responses: dict[str, Any] = {}

    def respond(self, kind: str, rc: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.responses[kind] = (rc, stdout, stderr)

    def raise_on(self, kind: str, exc: BaseException) -> None:
        self.responses[kind] = exc

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert isinstance(argv, list), "argv must be a list"
        assert argv[0] == "nmcli"
        assert "shell" not in kwargs, "shell= must never be used"
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True
        assert isinstance(kwargs.get("timeout"), (int, float)) and kwargs["timeout"] > 0
        self.calls.append(list(argv))
        self.kwargs.append(dict(kwargs))
        resp = self.responses.get(_kind(argv), (0, "", ""))
        if isinstance(resp, BaseException):
            raise resp
        rc, out, err = resp
        return subprocess.CompletedProcess(argv, rc, out, err)

    def kinds(self) -> list[str]:
        return [_kind(c) for c in self.calls]

    def calls_of(self, kind: str) -> list[list[str]]:
        return [c for c in self.calls if _kind(c) == kind]


def _cred(
    ssid: str = SSID,
    password: str | None = PW,
    security: Security = Security.WPA,
    hidden: bool = False,
) -> WifiCredential:
    return WifiCredential(ssid=ssid, password=password, security=security, hidden=hidden)


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def backend(runner: FakeRunner) -> NetworkManagerBackend:
    return NetworkManagerBackend(iface="wlan0", runner=runner, timeout=30)


def _assert_no_password(caplog: pytest.LogCaptureFixture) -> None:
    assert PW not in caplog.text
    for rec in caplog.records:
        assert PW not in rec.getMessage()
        assert PW not in str(rec.args)


# --------------------------------------------------------------------------- #
# profile_name / ConnectResult
# --------------------------------------------------------------------------- #


def test_nmcli_runs_with_c_locale(backend: NetworkManagerBackend, runner: FakeRunner) -> None:
    """nmcli translates the ACTIVE column even in terse mode; force LC_ALL=C."""
    runner.respond("wifi", stdout="yes:Home\n")
    backend.current_ssid()
    env = runner.kwargs[-1]["env"]
    assert env["LC_ALL"] == "C" and env["LANG"] == "C" and "LANGUAGE" not in env


def test_profile_name() -> None:
    assert profile_name("Home") == "wifi-qr-Home"
    assert profile_name("a b") == "wifi-qr-a b"


def test_connect_result_defaults() -> None:
    r = ConnectResult(ok=True)
    assert r.reason == ""
    assert r.addresses == ()


# --------------------------------------------------------------------------- #
# apply(): argv construction
# --------------------------------------------------------------------------- #

ADD_BASE = [
    "nmcli", "connection", "add", "type", "wifi",
    "con-name", NAME, "ifname", "wlan0", "ssid", SSID,
]
UP = ["nmcli", "--wait", "30", "connection", "up", "id", NAME]
SHOW = ["nmcli", "-t", "-f", "NAME", "connection", "show"]


def test_apply_wpa_argv(backend: NetworkManagerBackend, runner: FakeRunner) -> None:
    result = backend.apply(_cred(security=Security.WPA))
    assert result.ok
    assert runner.calls[0] == SHOW
    assert runner.calls[1] == ADD_BASE + ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", PW]
    assert runner.calls[2] == UP
    assert runner.kinds() == ["show", "add", "up", "device-show"]


def test_apply_sae_argv(backend: NetworkManagerBackend, runner: FakeRunner) -> None:
    backend.apply(_cred(security=Security.SAE))
    assert runner.calls[1] == ADD_BASE + ["wifi-sec.key-mgmt", "sae", "wifi-sec.psk", PW]


def test_apply_wep_argv(backend: NetworkManagerBackend, runner: FakeRunner) -> None:
    backend.apply(_cred(security=Security.WEP))
    assert runner.calls[1] == ADD_BASE + [
        "wifi-sec.key-mgmt", "none",
        "wifi-sec.wep-key-type", "1",
        "wifi-sec.wep-key0", PW,
    ]


def test_apply_open_argv(backend: NetworkManagerBackend, runner: FakeRunner) -> None:
    backend.apply(_cred(password=None, security=Security.OPEN))
    assert runner.calls[1] == ADD_BASE


def test_apply_hidden_argv(backend: NetworkManagerBackend, runner: FakeRunner) -> None:
    backend.apply(_cred(security=Security.WPA, hidden=True))
    assert runner.calls[1] == ADD_BASE + [
        "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", PW,
        "802-11-wireless.hidden", "yes",
    ]


def test_apply_open_hidden_argv(backend: NetworkManagerBackend, runner: FakeRunner) -> None:
    backend.apply(_cred(password=None, security=Security.OPEN, hidden=True))
    assert runner.calls[1] == ADD_BASE + ["802-11-wireless.hidden", "yes"]


def test_apply_uses_custom_iface_and_timeout(runner: FakeRunner) -> None:
    be = NetworkManagerBackend(iface="wlan1", runner=runner, timeout=45)
    be.apply(_cred())
    add = runner.calls_of("add")[0]
    assert add[add.index("ifname") + 1] == "wlan1"
    up = runner.calls_of("up")[0]
    assert up[:3] == ["nmcli", "--wait", "45"]
    # The subprocess timeout for `up` must not race nmcli's own --wait.
    up_kwargs = runner.kwargs[runner.kinds().index("up")]
    assert up_kwargs["timeout"] > 45


def test_apply_never_uses_shell(backend: NetworkManagerBackend, runner: FakeRunner) -> None:
    backend.apply(_cred())
    for kw in runner.kwargs:
        assert "shell" not in kw
        assert kw["capture_output"] is True
        assert kw["text"] is True


# --------------------------------------------------------------------------- #
# apply(): existing profile handling
# --------------------------------------------------------------------------- #


def test_existing_profile_is_deleted_before_add(
    backend: NetworkManagerBackend, runner: FakeRunner
) -> None:
    runner.respond("show", stdout="Wired connection 1\nwifi-qr-Home\nwifi-qr-Other\n")
    backend.apply(_cred())
    assert runner.kinds() == ["show", "delete", "add", "up", "device-show"]
    assert runner.calls[1] == ["nmcli", "connection", "delete", "id", NAME]


def test_missing_profile_is_not_deleted(
    backend: NetworkManagerBackend, runner: FakeRunner
) -> None:
    runner.respond("show", stdout="Wired connection 1\nwifi-qr-Other\n")
    backend.apply(_cred())
    assert "delete" not in runner.kinds()


def test_existing_profile_with_escaped_colon(runner: FakeRunner) -> None:
    be = NetworkManagerBackend(runner=runner)
    runner.respond("show", stdout="wifi-qr-cafe\\:guest\n")
    be.apply(_cred(ssid="cafe:guest"))
    assert runner.calls[1] == ["nmcli", "connection", "delete", "id", "wifi-qr-cafe:guest"]


def test_profile_show_failure_does_not_abort_apply(
    backend: NetworkManagerBackend, runner: FakeRunner
) -> None:
    runner.respond("show", rc=1, stderr="Error: could not list")
    result = backend.apply(_cred())
    assert result.ok
    assert "add" in runner.kinds()


# --------------------------------------------------------------------------- #
# apply(): failure paths and cleanup
# --------------------------------------------------------------------------- #


def test_add_failure_cleans_up_and_masks_reason(
    backend: NetworkManagerBackend, runner: FakeRunner
) -> None:
    runner.respond("add", rc=1, stderr=f"Error: invalid psk {PW}\nsecond line {PW}")
    result = backend.apply(_cred())
    assert result == ConnectResult(ok=False, reason="Error: invalid psk ***")
    assert runner.kinds() == ["show", "add", "delete"]
    assert runner.calls[2] == ["nmcli", "connection", "delete", "id", NAME]


def test_up_failure_cleans_up_and_masks_reason(
    backend: NetworkManagerBackend, runner: FakeRunner
) -> None:
    runner.respond("up", rc=4, stderr=f"Error: Connection activation failed ({PW})\n")
    result = backend.apply(_cred())
    assert not result.ok
    assert result.reason == "Error: Connection activation failed (***)"
    assert result.addresses == ()
    assert runner.kinds() == ["show", "add", "up", "delete"]


def test_failure_reason_falls_back_to_stdout(
    backend: NetworkManagerBackend, runner: FakeRunner
) -> None:
    runner.respond("up", rc=4, stdout="Connection activation failed: no secrets\n", stderr="")
    result = backend.apply(_cred())
    assert result.reason == "Connection activation failed: no secrets"


def test_failure_reason_when_no_output(
    backend: NetworkManagerBackend, runner: FakeRunner
) -> None:
    runner.respond("up", rc=10)
    result = backend.apply(_cred())
    assert not result.ok
    assert "10" in result.reason


def test_cleanup_delete_failure_is_ignored(
    backend: NetworkManagerBackend, runner: FakeRunner
) -> None:
    runner.respond("up", rc=4, stderr="boom")
    runner.respond("delete", rc=10, stderr="cannot delete")
    result = backend.apply(_cred())
    assert result == ConnectResult(ok=False, reason="boom")


def test_cleanup_delete_exception_is_ignored(
    backend: NetworkManagerBackend, runner: FakeRunner
) -> None:
    runner.respond("up", rc=4, stderr="boom")
    runner.raise_on("delete", subprocess.TimeoutExpired(cmd=["nmcli"], timeout=30))
    result = backend.apply(_cred())
    assert result == ConnectResult(ok=False, reason="boom")


def test_timeout_expired_is_wrapped_and_masked(
    backend: NetworkManagerBackend, runner: FakeRunner
) -> None:
    argv_with_pw = UP + ["wifi-sec.psk", PW]
    runner.raise_on("up", subprocess.TimeoutExpired(cmd=argv_with_pw, timeout=40))
    result = backend.apply(_cred())
    assert not result.ok
    assert PW not in result.reason
    assert "timed out" in result.reason
    # The half-created profile is removed.
    assert runner.kinds() == ["show", "add", "up", "delete"]


def test_oserror_is_wrapped(backend: NetworkManagerBackend, runner: FakeRunner) -> None:
    exc = FileNotFoundError(2, "No such file or directory", "nmcli")
    for kind in ("show", "delete", "add", "up"):
        runner.raise_on(kind, exc)
    result = backend.apply(_cred())
    assert not result.ok
    assert "nmcli" in result.reason
    assert PW not in result.reason


def test_oserror_message_is_masked(backend: NetworkManagerBackend, runner: FakeRunner) -> None:
    runner.raise_on("add", OSError(f"cannot spawn with {PW}"))
    result = backend.apply(_cred())
    assert not result.ok
    assert PW not in result.reason
    assert "***" in result.reason


def test_value_error_from_runner_is_reported_not_raised(
    backend: NetworkManagerBackend, runner: FakeRunner, caplog: pytest.LogCaptureFixture
) -> None:
    # subprocess raises ValueError("embedded null byte") for a NUL in argv.
    # The parser rejects such values first; this is the belt-and-braces path
    # so that no payload can ever take the daemon loop down.
    runner.raise_on("show", ValueError("embedded null byte"))
    with caplog.at_level(logging.DEBUG):
        result = backend.apply(_cred(ssid="Ca\x00fe"))
    assert result == ConnectResult(ok=False, reason="invalid characters in credential")
    # nothing was added, so nothing is deleted (a delete with the same argv
    # would raise the same ValueError again)
    assert runner.kinds() == ["show"]
    _assert_no_password(caplog)


def test_value_error_from_cleanup_delete_is_ignored(
    backend: NetworkManagerBackend, runner: FakeRunner
) -> None:
    runner.respond("up", rc=4, stderr="boom")
    runner.raise_on("delete", ValueError("embedded null byte"))
    result = backend.apply(_cred())
    assert result == ConnectResult(ok=False, reason="boom")


# --------------------------------------------------------------------------- #
# apply(): success
# --------------------------------------------------------------------------- #


def test_success_returns_ip_addresses(
    backend: NetworkManagerBackend, runner: FakeRunner
) -> None:
    runner.respond(
        "device-show",
        stdout="IP4.ADDRESS[1]:192.168.1.23/24\nIP4.ADDRESS[2]:10.0.0.5/8\n",
    )
    result = backend.apply(_cred())
    assert result == ConnectResult(ok=True, addresses=("192.168.1.23/24", "10.0.0.5/8"))
    assert runner.calls[-1] == ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", "wlan0"]


def test_success_survives_ip_lookup_failure(
    backend: NetworkManagerBackend, runner: FakeRunner
) -> None:
    runner.raise_on("device-show", OSError("gone"))
    result = backend.apply(_cred())
    assert result.ok
    assert result.addresses == ()


# --------------------------------------------------------------------------- #
# current_ssid()
# --------------------------------------------------------------------------- #


def test_current_ssid_returns_active(backend: NetworkManagerBackend, runner: FakeRunner) -> None:
    runner.respond("device-wifi", stdout="no:Neighbour\nyes:Home\nno:Other\n")
    assert backend.current_ssid() == "Home"
    assert runner.calls[0] == ["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi"]


def test_current_ssid_unescapes_colon(backend: NetworkManagerBackend, runner: FakeRunner) -> None:
    runner.respond("device-wifi", stdout="no:Foo\nyes:cafe\\:guest\\\\x\n")
    assert backend.current_ssid() == "cafe:guest\\x"


def test_current_ssid_none_when_no_active(
    backend: NetworkManagerBackend, runner: FakeRunner
) -> None:
    runner.respond("device-wifi", stdout="no:Neighbour\nno:Other\n")
    assert backend.current_ssid() is None


def test_current_ssid_none_on_empty_output(
    backend: NetworkManagerBackend, runner: FakeRunner
) -> None:
    runner.respond("device-wifi", stdout="")
    assert backend.current_ssid() is None


def test_current_ssid_none_on_nonzero_exit(
    backend: NetworkManagerBackend, runner: FakeRunner
) -> None:
    runner.respond("device-wifi", rc=8, stderr="Error: NetworkManager is not running.")
    assert backend.current_ssid() is None


def test_current_ssid_none_on_runner_error(
    backend: NetworkManagerBackend, runner: FakeRunner
) -> None:
    runner.raise_on("device-wifi", FileNotFoundError("nmcli"))
    assert backend.current_ssid() is None
    runner.raise_on("device-wifi", subprocess.TimeoutExpired(cmd=["nmcli"], timeout=30))
    assert backend.current_ssid() is None


# --------------------------------------------------------------------------- #
# ip_addresses()
# --------------------------------------------------------------------------- #


def test_ip_addresses_parses_prefixes(
    backend: NetworkManagerBackend, runner: FakeRunner
) -> None:
    runner.respond(
        "device-show",
        stdout="IP4.ADDRESS[1]:192.168.1.23/24\nIP4.ADDRESS[2]:10.0.0.5/8\n\n",
    )
    assert backend.ip_addresses() == ("192.168.1.23/24", "10.0.0.5/8")


def test_ip_addresses_empty(backend: NetworkManagerBackend, runner: FakeRunner) -> None:
    runner.respond("device-show", stdout="")
    assert backend.ip_addresses() == ()
    runner.respond("device-show", rc=10, stderr="Error: Device 'wlan0' not found.")
    assert backend.ip_addresses() == ()


def test_ip_addresses_on_runner_error(
    backend: NetworkManagerBackend, runner: FakeRunner
) -> None:
    runner.raise_on("device-show", OSError("nmcli"))
    assert backend.ip_addresses() == ()


# --------------------------------------------------------------------------- #
# logging: the password must never appear in any record
# --------------------------------------------------------------------------- #


def test_no_password_in_logs_on_success(
    backend: NetworkManagerBackend, runner: FakeRunner, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="wifi_qr.network")
    backend.apply(_cred(security=Security.WPA))
    backend.apply(_cred(security=Security.WEP))
    assert caplog.records, "expected the backend to log something"
    _assert_no_password(caplog)
    # argv is logged, but with the secret masked.
    assert any("wifi-sec.psk" in r.getMessage() and "***" in r.getMessage() for r in caplog.records)


def test_no_password_in_logs_on_failure(
    backend: NetworkManagerBackend, runner: FakeRunner, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="wifi_qr.network")
    runner.respond("add", rc=1, stderr=f"psk {PW} rejected")
    backend.apply(_cred())
    runner.respond("add", rc=0)
    runner.raise_on("up", subprocess.TimeoutExpired(cmd=UP + ["wifi-sec.psk", PW], timeout=40))
    backend.apply(_cred())
    runner.raise_on("up", OSError(f"spawn failed {PW}"))
    backend.apply(_cred())
    _assert_no_password(caplog)


# --------------------------------------------------------------------------- #
# DryRunBackend
# --------------------------------------------------------------------------- #


def test_dry_run_backend(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="wifi_qr.network")
    be = DryRunBackend()
    result = be.apply(_cred())
    assert result == ConnectResult(ok=True, reason="dry-run")
    assert be.current_ssid() is None
    assert any(SSID in r.getMessage() for r in caplog.records)
    _assert_no_password(caplog)


def test_backends_satisfy_protocol() -> None:
    # Structural check: both backends expose the NetworkBackend surface.
    for cls in (NetworkManagerBackend, DryRunBackend):
        assert callable(getattr(cls, "apply"))
        assert callable(getattr(cls, "current_ssid"))
    assert hasattr(network, "NetworkBackend")
