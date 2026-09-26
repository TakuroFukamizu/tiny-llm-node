"""Tests for wifi_qr.daemon.

The scanner is ``FakeScanner`` (or small subclasses that raise ``OSError``)
and the backend is ``FakeBackend``; every state transition, the dedupe
rule, READY==2 handling (read like READY==1), the I2C error budget and the
feedback events are asserted without hardware or nmcli.
"""

from __future__ import annotations

import logging

import pytest

from wifi_qr.daemon import Daemon, Feedback, FeedbackEvent, NullFeedback
from wifi_qr.network import ConnectResult
from wifi_qr.payload import Security, WifiCredential
from wifi_qr.scanner import FakeScanner, ScannerError

PW = "s3cretPassw0rd"
PAYLOAD = f"WIFI:T:WPA;S:Cafe;P:{PW};;".encode()
CRED = WifiCredential(ssid="Cafe", password=PW, security=Security.WPA)


class FakeBackend:
    """Records applies; serves results from ``results`` (default ok)."""

    def __init__(self, results: list[ConnectResult] | None = None, ssid: str | None = None):
        self.results = list(results or [])
        self.ssid = ssid
        self.applied: list[WifiCredential] = []
        self.current_ssid_calls = 0

    def apply(self, cred: WifiCredential) -> ConnectResult:
        self.applied.append(cred)
        if self.results:
            return self.results.pop(0)
        return ConnectResult(ok=True, addresses=("192.0.2.10/24",))

    def current_ssid(self) -> str | None:
        self.current_ssid_calls += 1
        return self.ssid


class RecordingFeedback:
    def __init__(self) -> None:
        self.events: list[FeedbackEvent] = []

    def on_event(self, event: FeedbackEvent) -> None:
        self.events.append(event)


class ExplodingFeedback(RecordingFeedback):
    def on_event(self, event: FeedbackEvent) -> None:
        super().on_event(event)
        raise RuntimeError("led driver broke")


class FlakyReadyScanner(FakeScanner):
    """``ready()`` raises OSError for the first ``failures`` calls."""

    def __init__(self, failures: int, **kw) -> None:
        super().__init__(**kw)
        self.failures = failures
        self.ready_calls = 0

    def ready(self) -> int:
        self.ready_calls += 1
        if self.failures > 0:
            self.failures -= 1
            raise OSError(121, "Remote I/O error")
        return super().ready()


class FlakyStartScanner(FakeScanner):
    """``set_trigger_mode`` raises OSError for the first ``failures`` calls."""

    def __init__(self, failures: int, **kw) -> None:
        super().__init__(**kw)
        self.failures = failures

    def set_trigger_mode(self, auto: bool) -> None:
        if self.failures > 0:
            self.failures -= 1
            raise OSError(6, "No such device or address")
        super().set_trigger_mode(auto)


class SleepRecorder:
    def __init__(self, interrupt_after: int | None = None) -> None:
        self.calls: list[float] = []
        self.interrupt_after = interrupt_after

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self.interrupt_after is not None and len(self.calls) >= self.interrupt_after:
            raise KeyboardInterrupt


def make(scanner, backend=None, feedback=None, **kw):
    backend = backend if backend is not None else FakeBackend()
    feedback = feedback if feedback is not None else RecordingFeedback()
    sleep = kw.pop("sleep", SleepRecorder())
    daemon = Daemon(scanner, backend, feedback=feedback, sleep=sleep, **kw)
    return daemon, backend, feedback, sleep


# --------------------------------------------------------------------------- #
# protocol / defaults
# --------------------------------------------------------------------------- #


def test_feedback_events_exist():
    names = {e.name for e in FeedbackEvent}
    assert names == {
        "SCANNER_READY", "SCAN_RECEIVED", "APPLYING", "CONNECTED", "FAILED", "SCANNER_ERROR",
    }


def test_null_feedback_is_a_feedback_and_does_nothing():
    fb = NullFeedback()
    assert isinstance(fb, Feedback)
    for event in FeedbackEvent:
        assert fb.on_event(event) is None


def test_default_feedback_is_null():
    daemon = Daemon(FakeScanner([PAYLOAD]), FakeBackend())
    daemon.start()
    assert daemon.step() is True


# --------------------------------------------------------------------------- #
# start()
# --------------------------------------------------------------------------- #


def test_start_sets_auto_mode_and_emits_ready(caplog):
    scanner = FakeScanner()
    daemon, _, fb, sleep = make(scanner)
    with caplog.at_level(logging.INFO, logger="wifi_qr.daemon"):
        daemon.start()
    assert scanner.trigger_mode_calls == [True]
    assert fb.events == [FeedbackEvent.SCANNER_READY]
    assert sleep.calls == []
    assert "firmware" in caplog.text.lower()


def test_start_retries_then_succeeds():
    scanner = FlakyStartScanner(failures=2)
    daemon, _, fb, sleep = make(scanner, startup_retries=5, startup_retry_delay=5.0)
    daemon.start()
    assert scanner.trigger_mode_calls == [True]
    assert sleep.calls == [5.0, 5.0]
    assert fb.events == [
        FeedbackEvent.SCANNER_ERROR,
        FeedbackEvent.SCANNER_ERROR,
        FeedbackEvent.SCANNER_READY,
    ]


def test_start_exhausts_retries_and_raises():
    scanner = FlakyStartScanner(failures=100)
    daemon, _, fb, sleep = make(scanner, startup_retries=3, startup_retry_delay=0.5)
    with pytest.raises(ScannerError):
        daemon.start()
    # 1 initial attempt + 3 retries, each retry preceded by one sleep.
    assert sleep.calls == [0.5, 0.5, 0.5]
    assert FeedbackEvent.SCANNER_READY not in fb.events
    assert FeedbackEvent.SCANNER_ERROR in fb.events


def test_start_survives_unreadable_firmware_version(caplog):
    # Spec section 2: the FW-version register address is unverified and is
    # diagnostic only; a NACK there must not fail start-up.
    class NoFwScanner(FakeScanner):
        def firmware_version(self) -> int:
            raise OSError(121, "Remote I/O error")

    scanner = NoFwScanner()
    daemon, _, fb, sleep = make(scanner, startup_retries=2, startup_retry_delay=5.0)
    with caplog.at_level(logging.INFO, logger="wifi_qr.daemon"):
        daemon.start()
    assert scanner.trigger_mode_calls == [True]
    assert sleep.calls == []
    assert fb.events == [FeedbackEvent.SCANNER_READY]
    assert "scanner ready: firmware=unknown trigger_mode=auto" in caplog.text
    assert "firmware version unreadable" in caplog.text


def test_run_returns_1_when_start_fails():
    scanner = FlakyStartScanner(failures=100)
    daemon, _, _, _ = make(scanner, startup_retries=1, startup_retry_delay=0.0)
    assert daemon.run() == 1


# --------------------------------------------------------------------------- #
# step(): happy path / failure / dedupe
# --------------------------------------------------------------------------- #


def test_happy_path_events_in_order(caplog):
    scanner = FakeScanner([PAYLOAD])
    daemon, backend, fb, _ = make(scanner)
    with caplog.at_level(logging.INFO, logger="wifi_qr.daemon"):
        daemon.start()
        assert daemon.step() is True
    assert backend.applied == [CRED]
    assert fb.events == [
        FeedbackEvent.SCANNER_READY,
        FeedbackEvent.SCAN_RECEIVED,
        FeedbackEvent.APPLYING,
        FeedbackEvent.CONNECTED,
    ]
    # RUNBOOK.md greps for these tokens; keep them stable.
    assert "scanner ready: firmware=0x00 trigger_mode=auto" in caplog.text
    assert (
        "scan received: WifiCredential(ssid='Cafe', password=***, security=wpa-psk, hidden=False)"
        in caplog.text
    )
    assert "applying: ssid='Cafe'" in caplog.text
    assert "connected: ssid='Cafe' addresses=192.0.2.10/24" in caplog.text
    assert PW not in caplog.text
    assert daemon.step() is False


def test_password_never_logged_at_debug(caplog):
    scanner = FakeScanner([PAYLOAD])
    daemon, _, _, _ = make(scanner)
    with caplog.at_level(logging.DEBUG):
        daemon.start()
        daemon.step()
    assert PW not in caplog.text
    for record in caplog.records:
        assert PW not in record.getMessage()
        assert PW not in repr(record.args)


def test_failure_path(caplog):
    scanner = FakeScanner([PAYLOAD])
    backend = FakeBackend(results=[ConnectResult(ok=False, reason="Secrets were required")])
    daemon, _, fb, _ = make(scanner, backend)
    with caplog.at_level(logging.WARNING, logger="wifi_qr.daemon"):
        daemon.start()
        assert daemon.step() is True
    assert fb.events[-2:] == [FeedbackEvent.APPLYING, FeedbackEvent.FAILED]
    assert "connect failed: ssid='Cafe' reason=Secrets were required" in caplog.text
    assert PW not in caplog.text


def test_failed_apply_is_retried_on_next_scan():
    scanner = FakeScanner([PAYLOAD, PAYLOAD])
    backend = FakeBackend(results=[ConnectResult(ok=False, reason="nope")], ssid="Cafe")
    daemon, _, _, _ = make(scanner, backend)
    daemon.start()
    daemon.step()
    daemon.step()
    assert len(backend.applied) == 2


def test_dedupe_ignores_same_cred_when_connected(caplog):
    scanner = FakeScanner([PAYLOAD, PAYLOAD])
    backend = FakeBackend(ssid="Cafe")
    daemon, _, fb, _ = make(scanner, backend)
    daemon.start()
    daemon.step()
    with caplog.at_level(logging.INFO, logger="wifi_qr.daemon"):
        assert daemon.step() is True
    assert len(backend.applied) == 1
    assert backend.current_ssid_calls == 1
    assert "already connected: ssid='Cafe'; ignoring" in caplog.text
    assert "applying" not in caplog.text
    assert fb.events == [
        FeedbackEvent.SCANNER_READY,
        FeedbackEvent.SCAN_RECEIVED,
        FeedbackEvent.APPLYING,
        FeedbackEvent.CONNECTED,
        FeedbackEvent.SCAN_RECEIVED,
    ]


def test_dedupe_does_not_query_ssid_for_new_cred():
    scanner = FakeScanner([PAYLOAD])
    backend = FakeBackend(ssid="Cafe")
    daemon, _, _, _ = make(scanner, backend)
    daemon.start()
    daemon.step()
    assert backend.current_ssid_calls == 0


def test_same_cred_but_disconnected_reapplies():
    scanner = FakeScanner([PAYLOAD, PAYLOAD])
    backend = FakeBackend(ssid=None)
    daemon, _, _, _ = make(scanner, backend)
    daemon.start()
    daemon.step()
    daemon.step()
    assert len(backend.applied) == 2


def test_same_cred_but_other_network_active_reapplies():
    scanner = FakeScanner([PAYLOAD, PAYLOAD])
    backend = FakeBackend(ssid="Elsewhere")
    daemon, _, _, _ = make(scanner, backend)
    daemon.start()
    daemon.step()
    daemon.step()
    assert len(backend.applied) == 2


def test_same_ssid_different_password_reapplies():
    other = b"WIFI:T:WPA;S:Cafe;P:anotherPassw0rd;;"
    scanner = FakeScanner([PAYLOAD, other])
    backend = FakeBackend(ssid="Cafe")
    daemon, _, _, _ = make(scanner, backend)
    daemon.start()
    daemon.step()
    daemon.step()
    assert len(backend.applied) == 2
    assert backend.applied[1].password == "anotherPassw0rd"


def test_scanner_cleared_after_apply_success_and_failure():
    scanner = FakeScanner([PAYLOAD, PAYLOAD])
    backend = FakeBackend(results=[ConnectResult(ok=True), ConnectResult(ok=False, reason="x")])
    daemon, _, _, _ = make(scanner, backend)
    daemon.start()
    daemon.step()
    assert scanner.cleared == 1
    daemon.step()
    assert scanner.cleared == 2


# --------------------------------------------------------------------------- #
# step(): READY register semantics
# --------------------------------------------------------------------------- #


def test_ready_zero_returns_false_without_reading():
    scanner = FakeScanner([PAYLOAD], ready_sequence=[0])
    daemon, backend, fb, _ = make(scanner)
    daemon.start()
    assert daemon.step() is False
    assert backend.applied == []
    assert scanner.payloads == [PAYLOAD]
    assert fb.events == [FeedbackEvent.SCANNER_READY]


def test_ready_two_is_read_like_one():
    # Firmware: READY saturates at 2 when a second decode arrived before the
    # host read the first; the buffer holds the latest decode and the value is
    # sticky until DATA is read. It must be read, not re-polled or discarded.
    scanner = FakeScanner([PAYLOAD], ready_sequence=[2, 0])
    daemon, backend, _, _ = make(scanner)
    daemon.start()
    assert daemon.step() is True
    assert backend.applied == [CRED]
    # only one READY read per step: the trailing 0 is still queued
    assert scanner.ready_sequence == [0]
    # cleared once by _apply, never by a READY==2 "give up" path
    assert scanner.cleared == 1


def test_ready_two_repeatedly_never_discards():
    scanner = FakeScanner([PAYLOAD, PAYLOAD], ready_sequence=[2, 2])
    daemon, backend, _, _ = make(scanner)
    daemon.start()
    assert daemon.step() is True
    assert daemon.step() is True
    assert backend.applied == [CRED, CRED]


def test_ready_two_then_one_reads():
    scanner = FakeScanner([PAYLOAD, PAYLOAD], ready_sequence=[2, 1])
    daemon, backend, _, _ = make(scanner)
    daemon.start()
    assert daemon.step() is True
    assert daemon.step() is True
    assert backend.applied == [CRED, CRED]


# --------------------------------------------------------------------------- #
# step(): bad payloads
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw",
    [
        b"WIFI:T:WPA;S:Cafe;P:hunter2;;",  # 7-char PSK -> PayloadError
        b"WIFI:T:WPA-EAP;S:Corp;I:alice;P:hunter2secret;;",  # UnsupportedAuth
        b"http://example.com/hunter2",  # not a WIFI payload
        b"\xff\xfehunter2\x00",  # undecodable garbage
    ],
)
def test_bad_payload_emits_failed_and_never_logs_secret(raw, caplog):
    scanner = FakeScanner([raw])
    daemon, backend, fb, _ = make(scanner)
    with caplog.at_level(logging.DEBUG):
        daemon.start()
        assert daemon.step() is True
    assert backend.applied == []
    assert fb.events == [
        FeedbackEvent.SCANNER_READY,
        FeedbackEvent.SCAN_RECEIVED,
        FeedbackEvent.FAILED,
    ]
    assert "hunter2" not in caplog.text
    assert "Cafe" not in caplog.text
    assert "alice" not in caplog.text
    assert "example.com" not in caplog.text
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_nul_inside_value_is_rejected_not_fatal(caplog):
    # A NUL inside S:/P: used to reach subprocess argv and raise ValueError
    # out of run(); it must land on the ordinary FAILED path instead.
    scanner = FakeScanner([b"WIFI:T:WPA;S:Ca\x00fe;P:hunter2secret;;"])
    daemon, backend, fb, _ = make(scanner)
    with caplog.at_level(logging.WARNING, logger="wifi_qr.daemon"):
        daemon.start()
        assert daemon.step() is True
    assert backend.applied == []
    assert fb.events[-1] == FeedbackEvent.FAILED
    assert "PayloadError" in caplog.text
    assert "hunter2" not in caplog.text


def test_bad_payload_log_mentions_error_type(caplog):
    scanner = FakeScanner([b"WIFI:T:WPA-EAP;S:Corp;P:hunter2secret;;"])
    daemon, _, _, _ = make(scanner)
    with caplog.at_level(logging.WARNING, logger="wifi_qr.daemon"):
        daemon.start()
        daemon.step()
    assert "UnsupportedAuth" in caplog.text
    assert "'WIFI:T:'" in caplog.text  # prefix up to the first key, never a value
    assert "WIFI:T:W" not in caplog.text


def test_ssid_first_payload_does_not_leak_value_chars(caplog):
    # Android emits S: first; a P: first ordering would otherwise leak a
    # password character inside the 8-char window.
    scanner = FakeScanner([b"WIFI:P:hunter2;S:Cafe;T:WPA;;"])
    daemon, _, _, _ = make(scanner)
    with caplog.at_level(logging.WARNING, logger="wifi_qr.daemon"):
        daemon.start()
        daemon.step()
    assert "'WIFI:P:'" in caplog.text
    assert "WIFI:P:h" not in caplog.text
    assert "hunter2" not in caplog.text


def test_non_wifi_payload_logs_length_not_text(caplog):
    scanner = FakeScanner([b"hello world"])
    daemon, _, _, _ = make(scanner)
    with caplog.at_level(logging.WARNING, logger="wifi_qr.daemon"):
        daemon.start()
        daemon.step()
    assert "hello" not in caplog.text
    assert "11" in caplog.text


def test_scanner_error_from_read_payload_returns_false(caplog):
    scanner = FakeScanner([], ready_sequence=[1])  # ready says 1 but no payload
    daemon, backend, fb, _ = make(scanner)
    with caplog.at_level(logging.WARNING, logger="wifi_qr.daemon"):
        daemon.start()
        assert daemon.step() is False
    assert backend.applied == []
    assert "no payload available" in caplog.text
    assert fb.events == [FeedbackEvent.SCANNER_READY]


# --------------------------------------------------------------------------- #
# I2C errors
# --------------------------------------------------------------------------- #


def test_oserror_emits_scanner_error_and_sleeps_one_second(caplog):
    scanner = FlakyReadyScanner(failures=1, payloads=[PAYLOAD])
    daemon, backend, fb, sleep = make(scanner)
    with caplog.at_level(logging.WARNING, logger="wifi_qr.daemon"):
        daemon.start()
        assert daemon.step() is False
    assert fb.events == [FeedbackEvent.SCANNER_READY, FeedbackEvent.SCANNER_ERROR]
    assert sleep.calls == [1.0]
    assert "scanner error (1/30): " in caplog.text
    assert "Remote I/O error" in caplog.text
    # recovers on the next poll
    assert daemon.step() is True
    assert backend.applied == [CRED]


def test_verbose_logs_ready_2(caplog):
    # RUNBOOK: "--verbose shows ready=2" is normal (two decodes between polls).
    scanner = FakeScanner([PAYLOAD], ready_sequence=[2])
    daemon, _, _, _ = make(scanner)
    with caplog.at_level(logging.DEBUG, logger="wifi_qr.daemon"):
        daemon.start()
        assert daemon.step() is True
    assert "ready=2" in caplog.text


def test_verbose_logs_ready_0_on_first_poll(caplog):
    # A unit that never raises READY must still be visible as ready=0 (once).
    scanner = FakeScanner(ready_sequence=[0, 0])
    daemon, _, _, _ = make(scanner)
    with caplog.at_level(logging.DEBUG, logger="wifi_qr.daemon"):
        daemon.start()
        daemon.step()
        daemon.step()
    assert caplog.text.count("ready=0") == 1


def test_oserror_count_resets_on_success():
    scanner = FlakyReadyScanner(failures=2, ready_sequence=[0, 0])
    daemon, _, fb, _ = make(scanner, max_i2c_errors=3)
    daemon.start()
    daemon.step()  # error 1
    daemon.step()  # error 2
    assert daemon.step() is False  # READY==0: a successful poll resets the count
    scanner.failures = 2
    daemon.step()  # error 1 again (would be 3 -> ScannerError without the reset)
    daemon.step()  # error 2
    assert daemon.step() is False  # READY==0 again
    assert fb.events.count(FeedbackEvent.SCANNER_ERROR) == 4


class BrokenDataScanner(FakeScanner):
    """``ready()`` answers 1 but every ``read_payload()`` raises OSError."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.read_calls = 0

    def read_payload(self) -> bytes:
        self.read_calls += 1
        raise OSError(121, "Remote I/O error")


def test_oserror_from_read_payload_counts_toward_budget(caplog):
    # READY answers but LENGTH/DATA reads fail: the budget must still be spent
    # (a successful READY read alone is not a successful step).
    scanner = BrokenDataScanner(ready_sequence=[1] * 5)
    daemon, _, fb, sleep = make(scanner, max_i2c_errors=3)
    daemon.start()
    with caplog.at_level(logging.WARNING, logger="wifi_qr.daemon"):
        assert daemon.step() is False
        assert daemon.step() is False
        with pytest.raises(ScannerError):
            daemon.step()
    assert scanner.read_calls == 3
    assert "scanner error (3/3)" in caplog.text
    assert fb.events.count(FeedbackEvent.SCANNER_ERROR) == 3
    assert sleep.calls.count(1.0) == 3
    # READY is left alone so a transient glitch can retry the same frame.
    assert scanner.cleared == 0


def test_oserror_count_resets_after_successful_read():
    class OneBadRead(FakeScanner):
        def __init__(self, **kw) -> None:
            super().__init__(**kw)
            self.fail_next = True

        def read_payload(self) -> bytes:
            if self.fail_next:
                self.fail_next = False
                raise OSError(121, "Remote I/O error")
            return super().read_payload()

    scanner = OneBadRead(payloads=[PAYLOAD], ready_sequence=[1, 1])
    daemon, backend, _, _ = make(scanner, max_i2c_errors=2)
    daemon.start()
    assert daemon.step() is False  # error 1
    assert daemon.step() is True  # full read succeeds -> budget reset
    assert backend.applied == [CRED]
    scanner.fail_next = True
    scanner.ready_sequence = [1]
    assert daemon.step() is False  # error 1 again, not 2 -> no ScannerError


def test_oserror_reaching_max_raises_scanner_error():
    scanner = FlakyReadyScanner(failures=100)
    daemon, _, fb, sleep = make(scanner, max_i2c_errors=3)
    daemon.start()
    daemon.step()
    daemon.step()
    with pytest.raises(ScannerError):
        daemon.step()
    assert fb.events.count(FeedbackEvent.SCANNER_ERROR) == 3
    assert sleep.calls.count(1.0) == 3


def test_run_returns_1_after_max_i2c_errors():
    scanner = FlakyReadyScanner(failures=100)
    daemon, _, fb, _ = make(scanner, max_i2c_errors=5)
    assert daemon.run() == 1
    assert fb.events.count(FeedbackEvent.SCANNER_ERROR) == 5


# --------------------------------------------------------------------------- #
# run()
# --------------------------------------------------------------------------- #


def test_run_once_stops_after_first_processed_payload():
    scanner = FakeScanner([PAYLOAD, PAYLOAD])
    daemon, backend, _, sleep = make(scanner, once=True)
    assert daemon.run() == 0
    assert backend.applied == [CRED]
    assert scanner.payloads == [PAYLOAD]


def test_run_once_keeps_polling_while_idle():
    scanner = FakeScanner([PAYLOAD], ready_sequence=[0, 0, 1])
    daemon, backend, _, sleep = make(scanner, once=True, poll_interval=0.05)
    assert daemon.run() == 0
    assert backend.applied == [CRED]
    assert sleep.calls == [0.05, 0.05]


def test_run_once_stops_with_1_on_bad_payload():
    scanner = FakeScanner([b"garbage", PAYLOAD])
    daemon, backend, _, _ = make(scanner, once=True)
    # --once stops after the first processed payload; a rejected one is a
    # failure (exit 1) and the second payload is left untouched.
    assert daemon.run() == 1
    assert backend.applied == []
    assert scanner.payloads == [PAYLOAD]


def test_run_once_returns_1_when_apply_fails():
    scanner = FakeScanner([PAYLOAD, PAYLOAD])
    backend = FakeBackend(results=[ConnectResult(ok=False, reason="no AP")])
    daemon, _, _, _ = make(scanner, backend, once=True)
    assert daemon.run() == 1
    assert len(backend.applied) == 1


def test_run_returns_0_on_keyboard_interrupt():
    scanner = FakeScanner()
    daemon, _, _, sleep = make(scanner, sleep=SleepRecorder(interrupt_after=3), poll_interval=0.2)
    assert daemon.run() == 0
    assert sleep.calls == [0.2, 0.2, 0.2]


# --------------------------------------------------------------------------- #
# feedback robustness
# --------------------------------------------------------------------------- #


def test_feedback_exceptions_are_swallowed(caplog):
    scanner = FakeScanner([PAYLOAD])
    fb = ExplodingFeedback()
    daemon, backend, _, _ = make(scanner, feedback=fb)
    with caplog.at_level(logging.WARNING, logger="wifi_qr.daemon"):
        daemon.start()
        assert daemon.step() is True
    assert backend.applied == [CRED]
    assert fb.events == [
        FeedbackEvent.SCANNER_READY,
        FeedbackEvent.SCAN_RECEIVED,
        FeedbackEvent.APPLYING,
        FeedbackEvent.CONNECTED,
    ]
    assert "led driver broke" in caplog.text
