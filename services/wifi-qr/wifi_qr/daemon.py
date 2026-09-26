"""Polling loop and state machine for wifi-qr.

``Daemon`` ties the three pure pieces together: it polls a
:class:`~wifi_qr.scanner.Scanner`, parses each decoded QR with
:func:`~wifi_qr.payload.parse_wifi_qr` and hands the credential to a
:class:`~wifi_qr.network.NetworkBackend`. Every dependency (scanner, backend,
feedback, ``sleep``) is injected so the whole loop is testable without I2C or
nmcli.

State machine (the unit is kept in AUTO trigger mode, so it scans all the time)::

    IDLE --(READY==1)--> READ --(parse ok)--> APPLY --(ok)--> IDLE
                            |                    |
                            +--(parse error)-----+--(fail)--> IDLE

Rules implemented here (design spec section 3, "daemon.py"):

* start-up writes AUTO trigger mode and logs the firmware version; if the
  bus does not answer it retries ``startup_retries`` times, ``startup_retry_delay``
  seconds apart, then raises :class:`~wifi_qr.scanner.ScannerError` so
  systemd's ``Restart=on-failure`` restarts the service;
* READY==2 ("read again") is re-read immediately; three consecutive 2s clear
  READY and drop the frame;
* dedupe: a credential equal to the last applied one, while
  ``backend.current_ssid()`` still reports that SSID, is ignored. Anything
  else (other SSID, same SSID with a different password, not connected) is
  applied again;
* while applying, no new scan is read; afterwards READY is cleared so a code
  scanned meanwhile is not applied by accident;
* ``OSError`` from the bus is logged and followed by a 1 s pause; after
  ``max_i2c_errors`` consecutive failures :class:`ScannerError` is raised
  (again leaving the restart to systemd).

Feedback hook (future status LED)
---------------------------------

The user decided *not* to drive the Raspberry Pi ACT LED from this service;
a node-wide status LED will be designed later (spec section 10). What this
module provides today is only the hook for it: :class:`FeedbackEvent`
enumerates the state transitions, :class:`Feedback` is the Protocol a LED
driver implements, and :class:`NullFeedback` is the do-nothing default wired
up in ``__main__``. When the status LED lands, add a class implementing
``Feedback`` and swap it in there; the daemon itself needs no change.
Feedback exceptions are caught and logged so a broken LED can never stop
provisioning.

Passwords never reach the log: credentials are logged through
``repr(WifiCredential)`` (which masks them) and rejected payloads are
described by exception type, length and at most the first 8 characters when
they start with ``WIFI:``. The raw payload text is never logged, not even at
DEBUG, because a malformed payload may still contain a password.

Log lines (INFO unless noted; RUNBOOK.md greps for these tokens)::

    scanner ready: firmware=0x.. trigger_mode=auto
    ready=<0|1|2>                                   (DEBUG, --verbose)
    scan received: WifiCredential(ssid='..', password=***, security=.., hidden=..)
    applying: ssid='..'
    connected: ssid='..' addresses=<a, b | ->
    connect failed: ssid='..' reason=<summarised nmcli error>   (WARNING)
    already connected: ssid='..'; ignoring
    scanner error (k/max): <OSError>                (WARNING)
"""

from __future__ import annotations

import logging
import time
from enum import Enum, auto
from typing import Callable, Protocol, runtime_checkable

from wifi_qr.network import NetworkBackend
from wifi_qr.payload import PayloadError, WifiCredential, parse_wifi_qr
from wifi_qr.scanner import READY_AGAIN, READY_DATA, READY_NONE, Scanner, ScannerError

logger = logging.getLogger("wifi_qr.daemon")

#: How many consecutive READY==2 answers we tolerate before clearing.
_READY_AGAIN_LIMIT = 3
#: Pause after an I2C OSError before polling again.
_I2C_ERROR_PAUSE = 1.0
#: Characters of a ``WIFI:`` payload that may be logged when it is rejected.
_PAYLOAD_LOG_PREFIX = 8


class FeedbackEvent(Enum):
    """State transitions a status indicator may want to show (spec section 10)."""

    SCANNER_READY = auto()  # start-up done, waiting for a QR
    SCAN_RECEIVED = auto()  # a QR was decoded and read from the unit
    APPLYING = auto()  # credential handed to NetworkManager
    CONNECTED = auto()  # apply succeeded
    FAILED = auto()  # bad payload or apply failed
    SCANNER_ERROR = auto()  # the I2C bus did not answer


@runtime_checkable
class Feedback(Protocol):
    """Receiver of :class:`FeedbackEvent` notifications (e.g. a future LED)."""

    def on_event(self, event: FeedbackEvent) -> None: ...


class NullFeedback:
    """Default feedback: does nothing."""

    def on_event(self, event: FeedbackEvent) -> None:
        return None


class Daemon:
    """Poll the scanner and apply scanned credentials.

    :param scanner: register-level scanner (real ``UnitQRCode`` or a fake).
    :param backend: network backend that applies credentials.
    :param feedback: event receiver; ``None`` means :class:`NullFeedback`.
    :param poll_interval: seconds between polls when nothing was processed.
    :param sleep: injectable ``time.sleep``.
    :param once: stop after the first credential has been applied.
    :param max_i2c_errors: consecutive ``OSError`` budget before giving up.
    :param startup_retries: number of *retries* after the first failed
        start-up attempt (12 retries = 13 attempts in total).
    :param startup_retry_delay: seconds between start-up attempts.
    """

    def __init__(
        self,
        scanner: Scanner,
        backend: NetworkBackend,
        feedback: Feedback | None = None,
        poll_interval: float = 0.2,
        sleep: Callable[[float], None] = time.sleep,
        once: bool = False,
        max_i2c_errors: int = 30,
        startup_retries: int = 12,
        startup_retry_delay: float = 5.0,
    ) -> None:
        self._scanner = scanner
        self._backend = backend
        self._feedback: Feedback = feedback if feedback is not None else NullFeedback()
        self._poll_interval = poll_interval
        self._sleep = sleep
        self._once = once
        self._max_i2c_errors = max_i2c_errors
        self._startup_retries = startup_retries
        self._startup_retry_delay = startup_retry_delay

        self._last_applied: WifiCredential | None = None
        #: Last READY value logged at DEBUG; None so the first poll always logs.
        self._last_ready: int | None = None
        self._i2c_errors = 0
        #: Set once a payload reached the parse/apply stage (for ``once``).
        self._processed_once = False

    # -- public API ----------------------------------------------------------- #

    def run(self) -> int:
        """Start, then poll until ``--once`` is satisfied or the loop dies.

        Returns a process exit code: 0 on a clean stop (``once`` connected, or
        ``KeyboardInterrupt``), 1 when the scanner gave up (start-up failure or
        the I2C error budget exhausted) or, with ``once``, when the single
        payload was rejected or its apply failed.
        """
        try:
            self.start()
            while True:
                processed = self.step()
                if self._once and self._processed_once:
                    return 0 if self._last_applied is not None else 1
                if not processed:
                    self._sleep(self._poll_interval)
        except KeyboardInterrupt:
            logger.info("interrupted; exiting")
            return 0
        except ScannerError as exc:
            logger.error("scanner gave up: %s", exc)
            return 1

    def start(self) -> None:
        """Put the unit in AUTO trigger mode and log its firmware version.

        Retries on ``OSError``/``ScannerError`` and raises ``ScannerError``
        once the retries are exhausted.
        """
        attempts = self._startup_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                self._scanner.set_trigger_mode(auto=True)
                version = self._scanner.firmware_version()
            except (OSError, ScannerError) as exc:
                self._emit(FeedbackEvent.SCANNER_ERROR)
                if attempt >= attempts:
                    raise ScannerError(
                        f"scanner did not respond after {attempts} attempts: {exc}"
                    ) from exc
                logger.warning(
                    "scanner error at start-up (attempt %d/%d): %s: %s; retrying in %ss",
                    attempt, attempts, type(exc).__name__, exc, self._startup_retry_delay,
                )
                self._sleep(self._startup_retry_delay)
                continue
            logger.info("scanner ready: firmware=0x%02x trigger_mode=auto", version)
            self._emit(FeedbackEvent.SCANNER_READY)
            return

    def step(self) -> bool:
        """One poll. Returns True when a payload was read and handled."""
        try:
            status = self._poll_ready()
            if status == READY_NONE:
                return False
            if status == READY_AGAIN:
                logger.warning(
                    "ready=2 for %d consecutive reads; clearing READY", _READY_AGAIN_LIMIT
                )
                self._scanner.clear()
                return False
            raw = self._scanner.read_payload()
        except OSError as exc:
            self._handle_i2c_error(exc)
            return False
        except ScannerError as exc:
            logger.warning("scanner rejected frame: %s", exc)
            return False

        logger.debug("scan received: %d bytes", len(raw))
        self._emit(FeedbackEvent.SCAN_RECEIVED)
        self._processed_once = True
        cred = self._parse(raw)
        if cred is not None:
            logger.info("scan received: %r", cred)
            self._apply(cred)
        return True

    # -- internals ------------------------------------------------------------ #

    def _poll_ready(self) -> int:
        """Read READY, re-reading immediately while it says 2 (up to the limit)."""
        status = self._scanner.ready()
        reads = 1
        while status == READY_AGAIN and reads < _READY_AGAIN_LIMIT:
            status = self._scanner.ready()
            reads += 1
        self._i2c_errors = 0
        # --verbose diagnostics without flooding: log the first poll, every
        # non-zero READY and each transition back to 0.
        if status != READY_NONE or status != self._last_ready:
            logger.debug("ready=%d", status)
        self._last_ready = status
        if status not in (READY_NONE, READY_DATA, READY_AGAIN):
            logger.debug("unexpected READY value %d; treating as data", status)
            return READY_DATA
        return status

    def _handle_i2c_error(self, exc: OSError) -> None:
        self._i2c_errors += 1
        logger.warning(
            "scanner error (%d/%d): %s", self._i2c_errors, self._max_i2c_errors, exc
        )
        self._emit(FeedbackEvent.SCANNER_ERROR)
        self._sleep(_I2C_ERROR_PAUSE)
        if self._i2c_errors >= self._max_i2c_errors:
            raise ScannerError(
                f"{self._i2c_errors} consecutive I2C errors; last: {exc}"
            ) from exc

    def _parse(self, raw: bytes) -> WifiCredential | None:
        """Decode and parse; log a masked description and emit FAILED on error."""
        text = raw.decode("utf-8", errors="replace")
        try:
            return parse_wifi_qr(text)
        except PayloadError as exc:
            logger.warning(
                "ignoring payload (%s): %s: %s",
                _describe_payload(text), type(exc).__name__, exc,
            )
            self._emit(FeedbackEvent.FAILED)
            return None

    def _apply(self, cred: WifiCredential) -> None:
        if cred == self._last_applied and self._backend.current_ssid() == cred.ssid:
            logger.info("already connected: ssid=%r; ignoring", cred.ssid)
            return

        logger.info("applying: ssid=%r", cred.ssid)
        self._emit(FeedbackEvent.APPLYING)
        result = self._backend.apply(cred)
        if result.ok:
            self._last_applied = cred
            logger.info(
                "connected: ssid=%r addresses=%s",
                cred.ssid, ", ".join(result.addresses) or "-",
            )
            self._emit(FeedbackEvent.CONNECTED)
        else:
            self._last_applied = None
            logger.warning("connect failed: ssid=%r reason=%s", cred.ssid, result.reason)
            self._emit(FeedbackEvent.FAILED)

        # Drop anything scanned while nmcli was busy.
        try:
            self._scanner.clear()
        except OSError as exc:
            logger.warning("could not clear READY after apply: %s", exc)

    def _emit(self, event: FeedbackEvent) -> None:
        """Notify feedback; a misbehaving receiver must never stop the loop."""
        try:
            self._feedback.on_event(event)
        except Exception as exc:  # noqa: BLE001 - deliberately broad
            logger.warning("feedback %s failed: %s: %s", event.name, type(exc).__name__, exc)


def _describe_payload(text: str) -> str:
    """Safe-to-log description of a rejected payload (never the whole text).

    For a ``WIFI:`` payload at most the first 8 characters are shown, cut
    before the first value (``WIFI:S:...`` would otherwise leak SSID or
    password characters); anything else is described by its length only.
    """
    stripped = text.lstrip()
    if not stripped.upper().startswith("WIFI:"):
        return f"not a WIFI: code, {len(text)} chars"
    head = stripped[:_PAYLOAD_LOG_PREFIX]
    value_start = head.find(":", len("WIFI:"))
    if value_start != -1:
        head = head[: value_start + 1]
    return f"starts with {head!r}, {len(text)} chars"
