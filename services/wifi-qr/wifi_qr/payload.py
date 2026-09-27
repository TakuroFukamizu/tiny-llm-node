"""Parser for the ZXing ``WIFI:`` QR payload used by phone WiFi sharing.

Format (as produced by Android's WiFi share and iOS shortcuts)::

    WIFI:T:WPA;S:MyNetwork;P:secret123;H:false;;

Key/value pairs are separated by ``;`` and the payload is terminated by
``;;``.  Inside values, ``;`` ``:`` ``,`` and ``\\`` are escaped with a
backslash.  Only ``T`` (security), ``S`` (SSID), ``P`` (password) and ``H``
(hidden) are used; the WPA-EAP keys ``E`` ``A`` ``I`` ``PH2`` are detected
and rejected as :class:`UnsupportedAuth`; other keys are ignored.

Control characters (C0, i.e. below 0x20, and DEL) inside the SSID or the
password are rejected as :class:`PayloadError`: a NUL can never be passed
to ``nmcli`` (``subprocess`` refuses argv with embedded NULs) and a newline
would break the line-based profile lookup and the log. WPA passphrases are
printable ASCII by IEEE 802.11 anyway; leading/trailing whitespace and NUL
padding around the whole payload are still stripped as before.

This module is pure: it never logs, prints, or includes password material
in exception messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

_PREFIX = "WIFI:"
_STRIP_CHARS = " \t\r\n\x00"
_EAP_KEYS = frozenset({"E", "A", "I", "PH2"})
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

_PSK_MIN = 8
_PSK_MAX = 63
_PSK_HEX_LEN = 64


class PayloadError(ValueError):
    """The text is not a valid, usable ``WIFI:`` payload."""


class UnsupportedAuth(PayloadError):
    """The payload requests an authentication scheme we do not support (WPA-EAP)."""


class Security(str, Enum):
    """WiFi security type. Values match NetworkManager ``key-mgmt`` names."""

    WPA = "wpa-psk"
    SAE = "sae"
    WEP = "wep"
    OPEN = "open"


_SECURITY_BY_T: dict[str, Security] = {
    "WPA": Security.WPA,
    "WPA2": Security.WPA,
    "WPA3": Security.WPA,  # "WPA3" alone means WPA2/WPA3 mixed mode.
    "SAE": Security.SAE,
    "WEP": Security.WEP,
    "NOPASS": Security.OPEN,
    "": Security.OPEN,
}


@dataclass(frozen=True)
class WifiCredential:
    """Parsed WiFi credentials. ``password`` is ``None`` iff ``security`` is OPEN."""

    ssid: str
    password: str | None
    security: Security
    hidden: bool = False

    def __repr__(self) -> str:
        """Log-safe rendering: the password is shown as ``***`` (``None`` when open).

        ``security`` is rendered as its value (``wpa-psk``/``sae``/``wep``/``open``)
        so log lines read the same as the nmcli ``key-mgmt`` names.
        """
        masked = None if self.password is None else "***"
        return (
            f"WifiCredential(ssid={self.ssid!r}, password={masked}, "
            f"security={self.security.value}, hidden={self.hidden})"
        )

    __str__ = __repr__


def parse_wifi_qr(text: str) -> WifiCredential:
    """Parse a ``WIFI:`` QR payload into a :class:`WifiCredential`.

    Raises :class:`UnsupportedAuth` for WPA-EAP payloads and
    :class:`PayloadError` for anything else that is malformed.
    """
    body = _strip_prefix(text.strip(_STRIP_CHARS))
    fields = _parse_fields(body)

    if "WPA-EAP" in fields.get("T", "").upper() or _EAP_KEYS & fields.keys():
        raise UnsupportedAuth("WPA-EAP (enterprise) networks are not supported")

    security = _parse_security(fields.get("T", ""))
    ssid = fields.get("S", "")
    if not ssid:
        raise PayloadError("payload has an empty or missing SSID (S)")
    if _has_control_chars(ssid):
        raise PayloadError("SSID (S) contains control characters")

    password = _validate_password(security, fields.get("P"))
    if password is not None and _has_control_chars(password):
        raise PayloadError("password (P) contains control characters")
    hidden = fields.get("H", "").lower() == "true"
    return WifiCredential(ssid=ssid, password=password, security=security, hidden=hidden)


def _strip_prefix(text: str) -> str:
    """Return the text after the (case-insensitive) ``WIFI:`` prefix."""
    if text[: len(_PREFIX)].upper() != _PREFIX:
        raise PayloadError("payload does not start with 'WIFI:'")
    return text[len(_PREFIX) :]


def _split_segments(body: str) -> list[str]:
    """Split on unescaped ``;`` while unescaping ``\\x`` -> ``x`` in the same pass.

    Empty segments (from the ``;;`` terminator or stray separators) are dropped,
    which makes the terminator lenient.
    """
    segments: list[str] = []
    current: list[str] = []
    chars = iter(body)
    for ch in chars:
        if ch == "\\":
            nxt = next(chars, None)
            if nxt is not None:
                current.append(nxt)
        elif ch == ";":
            segments.append("".join(current))
            current = []
        else:
            current.append(ch)
    segments.append("".join(current))
    return [seg for seg in segments if seg]


def _parse_fields(body: str) -> dict[str, str]:
    """Turn ``K:value`` segments into a dict with upper-cased keys.

    A later duplicate key overrides an earlier one. Segments without a ``:``
    are ignored.
    """
    fields: dict[str, str] = {}
    for segment in _split_segments(body):
        key, sep, value = segment.partition(":")
        if not sep:
            continue
        fields[key.strip().upper()] = value
    return fields


def _parse_security(t_value: str) -> Security:
    normalized = t_value.strip().upper()
    try:
        return _SECURITY_BY_T[normalized]
    except KeyError:
        raise PayloadError("unknown security type (T)") from None


def _validate_password(security: Security, password: str | None) -> str | None:
    """Apply per-security password rules; return the password to keep."""
    if security is Security.OPEN:
        return None
    if not password:
        raise PayloadError(f"{security.name} network requires a password (P)")
    if security is Security.WEP:
        return password  # WEP key lengths are not validated.
    if security is Security.SAE:
        # WPA3-SAE passwords have no 8-63 rule; NetworkManager exempts
        # key-mgmt=sae from WPA-PSK validation (nm-setting-wireless-security.c).
        return password
    if _PSK_MIN <= len(password) <= _PSK_MAX or _is_hex_psk(password):
        return password
    raise PayloadError(
        f"WPA passphrase must be {_PSK_MIN}-{_PSK_MAX} characters"
        f" or a {_PSK_HEX_LEN}-digit hex PSK"
    )


def _has_control_chars(value: str) -> bool:
    """True if ``value`` holds a C0 control character (< 0x20) or DEL (0x7f)."""
    return any(ord(ch) < 0x20 or ch == "\x7f" for ch in value)


def _is_hex_psk(password: str) -> bool:
    return len(password) == _PSK_HEX_LEN and set(password) <= _HEX_DIGITS
