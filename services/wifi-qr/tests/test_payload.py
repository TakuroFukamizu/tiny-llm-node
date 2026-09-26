"""Tests for wifi_qr.payload: the ZXing ``WIFI:`` QR payload parser."""

from __future__ import annotations

import pytest

from wifi_qr.payload import (
    PayloadError,
    Security,
    UnsupportedAuth,
    WifiCredential,
    parse_wifi_qr,
)

# --------------------------------------------------------------------------
# Happy paths
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Android's typical WiFi share output.
        (
            "WIFI:T:WPA;S:MyNet;P:pass1234;;",
            WifiCredential("MyNet", "pass1234", Security.WPA, False),
        ),
        # iOS-style output with an explicit H:false.
        (
            "WIFI:T:WPA;S:MyNet;P:pass1234;H:false;;",
            WifiCredential("MyNet", "pass1234", Security.WPA, False),
        ),
        # Escaped ';' in SSID, escaped ':' and '\\' in password.
        (
            r"WIFI:T:WPA;S:Caf\;e;P:pa\:ss\\word;;",
            WifiCredential("Caf;e", r"pa:ss\word", Security.WPA, False),
        ),
        # Escaped ',' as well.
        (
            r"WIFI:T:WPA;S:a\,b;P:c\,d\,e\,fgh;;",
            WifiCredential("a,b", "c,d,e,fgh", Security.WPA, False),
        ),
        # Unescaped ':' inside a value is allowed (split on first ':' only).
        (
            "WIFI:T:WPA;S:net:work;P:ab:cd:efgh;;",
            WifiCredential("net:work", "ab:cd:efgh", Security.WPA, False),
        ),
        # Open network.
        (
            "WIFI:T:nopass;S:OpenCafe;;",
            WifiCredential("OpenCafe", None, Security.OPEN, False),
        ),
        # Open network with a stray P: ignore the password.
        (
            "WIFI:T:nopass;S:OpenCafe;P:ignored;;",
            WifiCredential("OpenCafe", None, Security.OPEN, False),
        ),
        # Empty T and missing T both mean open.
        (
            "WIFI:T:;S:OpenCafe;;",
            WifiCredential("OpenCafe", None, Security.OPEN, False),
        ),
        (
            "WIFI:S:OpenCafe;;",
            WifiCredential("OpenCafe", None, Security.OPEN, False),
        ),
        # WPA3 / SAE.
        (
            "WIFI:T:SAE;S:MyWPA3;P:secret123;;",
            WifiCredential("MyWPA3", "secret123", Security.SAE, False),
        ),
        # WEP: no length validation.
        (
            "WIFI:T:WEP;S:OldNet;P:abcde;;",
            WifiCredential("OldNet", "abcde", Security.WEP, False),
        ),
        # Hidden SSID.
        (
            "WIFI:T:WPA;S:Hidden;P:secret123;H:true;;",
            WifiCredential("Hidden", "secret123", Security.WPA, True),
        ),
        # 64-hex PSK is accepted for WPA.
        (
            "WIFI:T:WPA;S:MyNet;P:" + "0123456789abcdef" * 4 + ";;",
            WifiCredential("MyNet", "0123456789abcdef" * 4, Security.WPA, False),
        ),
        # Boundary lengths: 8 and 63.
        (
            "WIFI:T:WPA;S:MyNet;P:12345678;;",
            WifiCredential("MyNet", "12345678", Security.WPA, False),
        ),
        (
            "WIFI:T:WPA;S:MyNet;P:" + "x" * 63 + ";;",
            WifiCredential("MyNet", "x" * 63, Security.WPA, False),
        ),
    ],
)
def test_parse_valid_payloads(text: str, expected: WifiCredential) -> None:
    assert parse_wifi_qr(text) == expected


@pytest.mark.parametrize(
    ("t_value", "expected"),
    [
        ("WPA", Security.WPA),
        ("wpa", Security.WPA),
        ("Wpa", Security.WPA),
        ("WPA2", Security.WPA),
        ("wpa2", Security.WPA),
        ("WPA3", Security.WPA),  # WPA3 alone means WPA2/WPA3 mixed -> WPA
        ("SAE", Security.SAE),
        ("sae", Security.SAE),
        ("WEP", Security.WEP),
        ("wep", Security.WEP),
    ],
)
def test_security_type_is_case_insensitive(t_value: str, expected: Security) -> None:
    cred = parse_wifi_qr(f"WIFI:T:{t_value};S:MyNet;P:pass1234;;")
    assert cred.security is expected
    assert cred.password == "pass1234"


@pytest.mark.parametrize("t_value", ["nopass", "NOPASS", "NoPass"])
def test_nopass_is_case_insensitive(t_value: str) -> None:
    cred = parse_wifi_qr(f"WIFI:T:{t_value};S:OpenCafe;;")
    assert cred.security is Security.OPEN
    assert cred.password is None


@pytest.mark.parametrize(
    ("h_value", "expected"),
    [
        ("true", True),
        ("TRUE", True),
        ("True", True),
        ("false", False),
        ("FALSE", False),
        ("", False),
        ("anything", False),
    ],
)
def test_hidden_flag(h_value: str, expected: bool) -> None:
    cred = parse_wifi_qr(f"WIFI:T:WPA;S:MyNet;P:pass1234;H:{h_value};;")
    assert cred.hidden is expected


def test_hidden_defaults_to_false_when_absent() -> None:
    assert parse_wifi_qr("WIFI:T:WPA;S:MyNet;P:pass1234;;").hidden is False


@pytest.mark.parametrize(
    "text",
    [
        "WIFI:T:WPA;S:MyNet;P:pass1234;;",  # ';;' terminator
        "WIFI:T:WPA;S:MyNet;P:pass1234;",  # single ';'
        "WIFI:T:WPA;S:MyNet;P:pass1234",  # no terminator
        "WIFI:T:WPA;S:MyNet;P:pass1234;;;",  # extra ';'
        "WIFI:T:WPA;;S:MyNet;P:pass1234;;",  # empty segment in the middle
    ],
)
def test_terminator_is_lenient(text: str) -> None:
    assert parse_wifi_qr(text) == WifiCredential("MyNet", "pass1234", Security.WPA)


@pytest.mark.parametrize(
    "text",
    [
        "WIFI:T:WPA;S:MyNet;P:pass1234;;\x00",
        "WIFI:T:WPA;S:MyNet;P:pass1234;;\r\n",
        "WIFI:T:WPA;S:MyNet;P:pass1234;;\n",
        "WIFI:T:WPA;S:MyNet;P:pass1234;;\r",
        "WIFI:T:WPA;S:MyNet;P:pass1234;;\x00\r\n",
        "WIFI:T:WPA;S:MyNet;P:pass1234;;\r\n\x00",
        "  WIFI:T:WPA;S:MyNet;P:pass1234;;  ",
        "\tWIFI:T:WPA;S:MyNet;P:pass1234;;\n",
    ],
)
def test_trailing_garbage_and_whitespace_are_stripped(text: str) -> None:
    assert parse_wifi_qr(text) == WifiCredential("MyNet", "pass1234", Security.WPA)


@pytest.mark.parametrize("prefix", ["WIFI:", "wifi:", "Wifi:"])
def test_prefix_is_case_insensitive(prefix: str) -> None:
    cred = parse_wifi_qr(f"{prefix}T:WPA;S:MyNet;P:pass1234;;")
    assert cred.ssid == "MyNet"


def test_unknown_keys_are_ignored() -> None:
    cred = parse_wifi_qr("WIFI:T:WPA;S:MyNet;X:whatever;P:pass1234;Z:1;;")
    assert cred == WifiCredential("MyNet", "pass1234", Security.WPA)


def test_keys_are_case_insensitive() -> None:
    cred = parse_wifi_qr("WIFI:t:wpa;s:MyNet;p:pass1234;h:true;;")
    assert cred == WifiCredential("MyNet", "pass1234", Security.WPA, True)


def test_inner_whitespace_in_values_is_preserved() -> None:
    cred = parse_wifi_qr("WIFI:T:WPA;S:My Net ;P: pass 1234;;")
    assert cred.ssid == "My Net "
    assert cred.password == " pass 1234"


# --------------------------------------------------------------------------
# Unsupported auth (WPA-EAP)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "WIFI:T:WPA-EAP;S:Corp;P:pass1234;;",
        "WIFI:T:wpa-eap;S:Corp;P:pass1234;;",
        "WIFI:T:WPA;S:Corp;P:pass1234;E:TTLS;;",
        "WIFI:T:WPA;S:Corp;P:pass1234;A:anon;;",
        "WIFI:T:WPA;S:Corp;P:pass1234;I:user;;",
        "WIFI:T:WPA;S:Corp;P:pass1234;PH2:MSCHAPV2;;",
        "WIFI:T:WPA;S:Corp;P:pass1234;e:TTLS;;",  # lowercase key
        # EAP detection wins over other validation (bad P length, empty S).
        "WIFI:T:WPA-EAP;S:Corp;P:x;;",
        "WIFI:T:WPA;S:;P:pass1234;E:TTLS;;",
    ],
)
def test_eap_is_rejected_as_unsupported(text: str) -> None:
    with pytest.raises(UnsupportedAuth):
        parse_wifi_qr(text)


def test_unsupported_auth_is_a_payload_error() -> None:
    assert issubclass(UnsupportedAuth, PayloadError)
    assert issubclass(PayloadError, ValueError)


# --------------------------------------------------------------------------
# Malformed payloads
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "T:WPA;S:MyNet;P:pass1234;;",  # no prefix
        "WIFI;T:WPA;S:MyNet;P:pass1234;;",  # wrong separator after WIFI
        "http://example.com",
        "WIFIX:T:WPA;S:MyNet;P:pass1234;;",
        "WIFI:T:WPA;S:;P:pass1234;;",  # empty S
        "WIFI:T:WPA;P:pass1234;;",  # missing S
        "WIFI:T:WPA;S:MyNet;P:;;",  # WPA empty P
        "WIFI:T:WPA;S:MyNet;;",  # WPA missing P
        "WIFI:T:SAE;S:MyNet;P:;;",  # SAE empty P
        "WIFI:T:SAE;S:MyNet;;",  # SAE missing P
        "WIFI:T:WEP;S:MyNet;P:;;",  # WEP empty P
        "WIFI:T:WEP;S:MyNet;;",  # WEP missing P
        "WIFI:T:WPA;S:MyNet;P:1234567;;",  # WPA P too short (7)
        "WIFI:T:WPA;S:MyNet;P:" + "x" * 64 + ";;",  # 64 chars, not hex
        "WIFI:T:WPA;S:MyNet;P:" + "x" * 65 + ";;",  # too long
        "WIFI:T:WPA;S:MyNet;P:" + "0" * 65 + ";;",  # hex but 65
        "WIFI:T:SAE;S:MyNet;P:1234567;;",  # SAE too short
        "WIFI:T:SAE;S:MyNet;P:" + "0123456789abcdef" * 4 + ";;",  # 64-hex only for WPA
        "WIFI:T:SAE;S:MyNet;P:" + "x" * 64 + ";;",
        "WIFI:T:WPA;S:MyNet;P:" + "0" * 63 + "g;;",  # 64, one non-hex
        "WIFI:T:WPA4;S:MyNet;P:pass1234;;",  # unknown T
        "WIFI:T:PSK;S:MyNet;P:pass1234;;",
        "WIFI:T:OPEN;S:MyNet;;",
        # Control characters inside a value: a NUL cannot cross argv at all
        # (subprocess raises ValueError) and a newline in the SSID breaks the
        # line-based profile lookup and the log, so both are rejected.
        "WIFI:T:WPA;S:Ca\x00fe;P:pass1234;;",
        "WIFI:T:WPA;S:MyNet;P:pass\x001234;;",
        "WIFI:T:WPA;S:Ca\nfe;P:pass1234;;",
        "WIFI:T:WPA;S:MyNet;P:pass\n1234;;",
        "WIFI:T:WPA;S:Ca\rfe;P:pass1234;;",
        "WIFI:T:WPA;S:Ca\x1bfe;P:pass1234;;",  # ESC
        "WIFI:T:WPA;S:MyNet;P:pass\x7f1234;;",  # DEL
        "WIFI:T:nopass;S:Open\x00Cafe;;",
        "WIFI:T:WEP;S:MyNet;P:ab\x00cd;;",
    ],
)
def test_malformed_payloads_raise_payload_error(text: str) -> None:
    with pytest.raises(PayloadError) as excinfo:
        parse_wifi_qr(text)
    assert not isinstance(excinfo.value, UnsupportedAuth)


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("WIFI:T:WPA;S:MyNet;P:short7;;", "short7"),
        ("WIFI:T:WPA;S:;P:leakyleaky;;", "leakyleaky"),
        ("WIFI:T:WPA-EAP;S:Corp;P:eapsecret;;", "eapsecret"),
        ("WIFI:T:BOGUS;S:MyNet;P:bogussecret;;", "bogussecret"),
        ("T:WPA;S:MyNet;P:noprefixsecret;;", "noprefixsecret"),
    ],
)
def test_error_messages_do_not_leak_password(text: str, secret: str) -> None:
    with pytest.raises(PayloadError) as excinfo:
        parse_wifi_qr(text)
    assert secret not in str(excinfo.value)
    assert secret not in repr(excinfo.value)


# --------------------------------------------------------------------------
# WifiCredential
# --------------------------------------------------------------------------


def test_repr_and_str_mask_password() -> None:
    cred = WifiCredential("MyNet", "supersecretpw", Security.WPA, False)
    for rendered in (repr(cred), str(cred), f"{cred}", f"{cred!r}"):
        assert "supersecretpw" not in rendered
        assert "***" in rendered
        assert "MyNet" in rendered
    # Enum value, not name, so logs read like nmcli key-mgmt.
    assert repr(cred) == "WifiCredential(ssid='MyNet', password=***, security=wpa-psk, hidden=False)"


def test_repr_masks_even_when_password_is_none() -> None:
    cred = WifiCredential("OpenCafe", None, Security.OPEN)
    rendered = repr(cred)
    assert "OpenCafe" in rendered
    # An open network has no secret to hide; showing None is informative.
    assert "password=None" in rendered


def test_credential_is_frozen() -> None:
    cred = WifiCredential("MyNet", "pass1234", Security.WPA)
    with pytest.raises(AttributeError):
        cred.ssid = "Other"  # type: ignore[misc]


def test_credential_equality_includes_password() -> None:
    a = WifiCredential("MyNet", "pass1234", Security.WPA)
    b = WifiCredential("MyNet", "pass1234", Security.WPA)
    c = WifiCredential("MyNet", "different1", Security.WPA)
    assert a == b
    assert hash(a) == hash(b)
    assert a != c


def test_security_enum_values_match_nmcli_key_mgmt() -> None:
    assert Security.WPA.value == "wpa-psk"
    assert Security.SAE.value == "sae"
    assert Security.WEP.value == "wep"
    assert Security.OPEN.value == "open"


def test_password_is_none_iff_open() -> None:
    open_cred = parse_wifi_qr("WIFI:T:nopass;S:OpenCafe;P:whatever;;")
    assert open_cred.password is None
    wpa_cred = parse_wifi_qr("WIFI:T:WPA;S:MyNet;P:pass1234;;")
    assert wpa_cred.password is not None
