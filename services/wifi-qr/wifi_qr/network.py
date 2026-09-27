"""NetworkManager (``nmcli``) backend for wifi-qr.

``NetworkManagerBackend.apply`` turns a :class:`~wifi_qr.payload.WifiCredential`
into a persistent NetworkManager profile named ``wifi-qr-<ssid>`` and activates
it. Every ``nmcli`` call goes through an injectable ``runner`` (``subprocess.run``
by default) so tests can assert the exact argv without touching the system.

Security note: the PSK is passed to ``nmcli`` as an argument (single-user root
appliance, momentary ``ps`` exposure is accepted by the spec). It must never
reach a log record or a ``ConnectResult.reason``; every string that could
contain it is passed through :func:`_mask` first, and ``TimeoutExpired`` /
``OSError`` messages (which embed the full argv) are never logged verbatim.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from wifi_qr.payload import Security, WifiCredential

logger = logging.getLogger("wifi_qr.network")


def nmcli_env() -> dict[str, str]:
    """Environment for nmcli subprocesses: the current one with a C locale.

    ``nmcli device wifi list`` translates the ACTIVE column even in terse
    mode (``_("yes")`` in src/nmcli/devices.c), so under a Japanese locale
    ``current_ssid()`` would look for ``yes:`` and never find ``はい:``.
    """
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    env.pop("LANGUAGE", None)
    return env

MASK = "***"
PROFILE_PREFIX = "wifi-qr-"

#: nmcli property names whose following argv value is a secret.
_SECRET_KEYS = frozenset({"wifi-sec.psk", "wifi-sec.wep-key0"})

#: Extra seconds the subprocess timeout for ``connection up`` gets on top of
#: nmcli's own ``--wait`` so nmcli can report its error before we kill it.
_UP_GRACE_SECONDS = 15

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class ConnectResult:
    """Outcome of applying a credential."""

    ok: bool
    reason: str = ""
    addresses: tuple[str, ...] = ()


class NetworkBackend(Protocol):
    """What the daemon needs from a network backend."""

    def apply(self, cred: WifiCredential) -> ConnectResult: ...

    def current_ssid(self) -> str | None: ...


def profile_name(ssid: str) -> str:
    """Name of the NetworkManager profile created for ``ssid``."""
    return PROFILE_PREFIX + ssid


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _mask(text: str, secret: str | None) -> str:
    """Replace every occurrence of ``secret`` in ``text`` with ``***``."""
    if not secret:
        return text
    return text.replace(secret, MASK)


def _mask_argv(argv: Sequence[str]) -> list[str]:
    """Copy of ``argv`` with the value following any secret key replaced."""
    masked: list[str] = []
    hide_next = False
    for arg in argv:
        if hide_next:
            masked.append(MASK)
            hide_next = False
            continue
        masked.append(arg)
        hide_next = arg in _SECRET_KEYS
    return masked


def _split_terse(line: str) -> list[str]:
    """Split one ``nmcli -t`` line on unescaped ``:`` and unescape ``\\x``."""
    fields: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            current.append(line[i + 1])
            i += 2
            continue
        if ch == ":":
            fields.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    fields.append("".join(current))
    return fields


def _unescape_line(line: str) -> str:
    """Unescape a whole ``nmcli -t`` line that holds a single field (e.g. NAME)."""
    out: list[str] = []
    i = 0
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line):
            out.append(line[i + 1])
            i += 2
        else:
            out.append(line[i])
            i += 1
    return "".join(out)


def _first_line(proc: subprocess.CompletedProcess[str], secret: str | None) -> str:
    """Masked first non-empty line of stderr, else stdout, else the exit code."""
    for stream in (proc.stderr, proc.stdout):
        for line in (stream or "").splitlines():
            line = line.strip()
            if line:
                return _mask(line, secret)
    return f"nmcli exited with status {proc.returncode}"


# --------------------------------------------------------------------------- #
# backends
# --------------------------------------------------------------------------- #


class NetworkManagerBackend:
    """Apply WiFi credentials through ``nmcli``.

    :param iface: wireless interface the profile is bound to.
    :param runner: ``subprocess.run``-compatible callable, injectable for tests.
    :param timeout: seconds passed to ``nmcli --wait`` for activation.
    """

    def __init__(
        self,
        iface: str = "wlan0",
        runner: Runner = subprocess.run,
        timeout: int = 30,
    ) -> None:
        self._iface = iface
        self._runner = runner
        self._timeout = timeout

    # -- public API ------------------------------------------------------- #

    def apply(self, cred: WifiCredential) -> ConnectResult:
        """Create (or replace) the profile for ``cred`` and bring it up."""
        name = profile_name(cred.ssid)
        secret = cred.password
        logger.info("applying %r as profile %s on %s", cred, name, self._iface)
        try:
            if self._profile_exists(name):
                logger.info("replacing existing profile %s", name)
                self._delete_profile(name)

            add = self._run(self._add_argv(cred, name), secret)
            if add.returncode != 0:
                reason = _first_line(add, secret)
                logger.warning("nmcli connection add failed: %s", reason)
                self._delete_profile_quietly(name)
                return ConnectResult(ok=False, reason=reason)

            up = self._run(
                ["nmcli", "--wait", str(self._timeout), "connection", "up", "id", name],
                secret,
                timeout=self._timeout + _UP_GRACE_SECONDS,
            )
            if up.returncode != 0:
                reason = _first_line(up, secret)
                logger.warning("nmcli connection up failed: %s", reason)
                self._delete_profile_quietly(name)
                return ConnectResult(ok=False, reason=reason)
        except subprocess.TimeoutExpired as exc:
            # str(exc) embeds the full argv including the PSK: never log it.
            reason = f"nmcli timed out after {exc.timeout}s"
            logger.warning("%s (profile %s)", reason, name)
            self._delete_profile_quietly(name)
            return ConnectResult(ok=False, reason=reason)
        except OSError as exc:
            reason = _mask(f"cannot run nmcli: {exc}", secret)
            logger.error("%s", reason)
            self._delete_profile_quietly(name)
            return ConnectResult(ok=False, reason=reason)
        except ValueError:
            # subprocess refuses argv with an embedded NUL ("embedded null
            # byte"). The parser rejects such credentials first; this guard
            # only makes sure a stray one can never take the loop down. No
            # cleanup: the failing argv is the first nmcli call for this
            # profile (show), and a delete would raise the same error.
            reason = "invalid characters in credential"
            logger.error("%s (profile not created)", reason)
            return ConnectResult(ok=False, reason=reason)

        addresses = self.ip_addresses()
        logger.info("connected to %s (%s)", cred.ssid, ", ".join(addresses) or "no IPv4 yet")
        return ConnectResult(ok=True, addresses=addresses)

    def current_ssid(self) -> str | None:
        """SSID of the active WiFi network on any device, or ``None``."""
        try:
            proc = self._run(["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi"], None)
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.debug("current_ssid: nmcli unavailable (%s)", type(exc).__name__)
            return None
        if proc.returncode != 0:
            return None
        for line in (proc.stdout or "").splitlines():
            fields = _split_terse(line)
            if len(fields) >= 2 and fields[0] == "yes":
                ssid = ":".join(fields[1:])
                return ssid or None
        return None

    def ip_addresses(self) -> tuple[str, ...]:
        """IPv4 addresses (CIDR) currently assigned to the interface."""
        try:
            proc = self._run(
                ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", self._iface], None
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.debug("ip_addresses: nmcli unavailable (%s)", type(exc).__name__)
            return ()
        if proc.returncode != 0:
            return ()
        addresses: list[str] = []
        for line in (proc.stdout or "").splitlines():
            if not line.startswith("IP4.ADDRESS"):
                continue
            _, _, value = line.partition(":")
            value = value.strip()
            if value:
                addresses.append(value)
        return tuple(addresses)

    # -- internals -------------------------------------------------------- #

    def _run(
        self, argv: list[str], secret: str | None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Invoke nmcli via the injected runner. Logs argv with secrets masked."""
        logger.debug("run: %s", " ".join(_mask_argv(argv)))
        proc = self._runner(
            argv,
            capture_output=True,
            text=True,
            timeout=self._timeout if timeout is None else timeout,
            env=nmcli_env(),
        )
        logger.debug("exit %s: %s", proc.returncode, " ".join(_mask_argv(argv[:5])))
        return proc

    def _add_argv(self, cred: WifiCredential, name: str) -> list[str]:
        argv = [
            "nmcli", "connection", "add", "type", "wifi",
            "con-name", name, "ifname", self._iface, "ssid", cred.ssid,
        ]
        argv += self._security_args(cred)
        if cred.hidden:
            argv += ["802-11-wireless.hidden", "yes"]
        return argv

    @staticmethod
    def _security_args(cred: WifiCredential) -> list[str]:
        pw = cred.password or ""
        if cred.security is Security.WPA:
            return ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", pw]
        if cred.security is Security.SAE:
            return ["wifi-sec.key-mgmt", "sae", "wifi-sec.psk", pw]
        if cred.security is Security.WEP:
            return [
                "wifi-sec.key-mgmt", "none",
                "wifi-sec.wep-key-type", "1",
                "wifi-sec.wep-key0", pw,
            ]
        return []

    def _profile_exists(self, name: str) -> bool:
        """True if a connection profile named ``name`` exists."""
        proc = self._run(["nmcli", "-t", "-f", "NAME", "connection", "show"], None)
        if proc.returncode != 0:
            logger.warning("could not list connection profiles: %s", _first_line(proc, None))
            return False
        return any(_unescape_line(line) == name for line in (proc.stdout or "").splitlines())

    def _delete_profile(self, name: str) -> None:
        proc = self._run(["nmcli", "connection", "delete", "id", name], None)
        if proc.returncode != 0:
            logger.warning("could not delete profile %s: %s", name, _first_line(proc, None))

    def _delete_profile_quietly(self, name: str) -> None:
        """Best-effort cleanup after a failed add/up; never raises."""
        try:
            self._delete_profile(name)
        except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
            logger.debug("cleanup of %s failed (%s)", name, type(exc).__name__)


class DryRunBackend:
    """Backend that only logs the (masked) credential and never touches nmcli."""

    def apply(self, cred: WifiCredential) -> ConnectResult:
        logger.info(
            "dry-run: would apply ssid=%r security=%s", cred.ssid, cred.security.value
        )
        return ConnectResult(ok=True, reason="dry-run")

    def current_ssid(self) -> str | None:
        return None
