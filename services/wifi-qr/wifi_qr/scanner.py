"""I2C driver for the M5Stack Unit QRCode (STM32F030) scanner.

The unit decodes QR codes on its own and exposes the result through a small
register file over I2C. This module reads that register file; it knows
nothing about WiFi (see ``payload.py`` and ``daemon.py``).

Wiring (Grove cable -> Raspberry Pi 5 40-pin header)
----------------------------------------------------

===========  ======  ==================  =====
Grove wire   Signal  Pi header           GPIO
===========  ======  ==================  =====
red          5V      pin 2 (or pin 4)    --
black        GND     pin 6               --
yellow       SDA     pin 3               GPIO2
white        SCL     pin 5               GPIO3
===========  ======  ==================  =====

The colours above are the M5Stack cable convention. Seeed-brand Grove
cables are the reverse (yellow = SCL, white = SDA), so go by position, not
colour: on every Grove connector the four wires sit in the fixed order
SCL, SDA, VCC, GND -- the signal wire next to red is SDA (pin 3), the
outermost signal wire, farthest from red, is SCL (pin 5). Swapping the two
is harmless (both lines are pulled up to 3V3); ``i2cdetect`` simply shows
nothing until they are the right way round.

The unit is powered from 5V but its I2C lines are pulled up to 3V3 on the
board, so they connect directly to the Pi without level shifting. Set the
unit's slide switch to I2C mode. Enable the bus with ``dtparam=i2c_arm=on``
(``raspi-config nonint do_i2c 0``); the device then answers at 0x21 on
``/dev/i2c-1`` (``i2cdetect -y 1``).

Register map (I2C address 0x21)
-------------------------------

Register addresses are 16 bits wide and are sent as TWO bytes,
little-endian (low byte first). A read is one ``i2c_rdwr`` transaction:
write ``[lo, hi]`` then a repeated-start read of ``n`` bytes. A write is a
single message ``[lo, hi, data...]``.

==============  ======  =====  ==========================================
Name            Addr    R/W    Meaning
==============  ======  =====  ==========================================
TRIGGER         0x0000  W      1 byte, start/stop scanning (manual mode)
READY           0x0010  R/W    0 = no data, 1 = data ready, 2 = data
                               ready and a second decode arrived since
                               the last read (buffer holds the latest;
                               read it like 1); reset by a DATA read or
                               by writing 0
LENGTH          0x0020  R      2 bytes LE, decoded payload length
TRIGGER_MODE    0x0030  R/W    0 = auto (always scanning; believed to be
                               the power-on default, unverified),
                               1 = manual (TRIGGER / button)
TRIGGER_KEY     0x0040  R      0 = button pressed, 1 = not pressed
FW_VERSION      0x00FE  R      1 byte, diagnostic only (see caveats)
DATA            0x1000  R      up to 512 payload bytes, read in ONE
                               transaction at 0x1000 (see below)
==============  ======  =====  ==========================================

The daemon puts the unit in AUTO trigger mode so it scans continuously;
the unit's buzzer beeps by itself on every successful decode. The red
aiming line is driven by the scan engine and may stay off while idle,
even on a correctly powered unit; do not use it as a power indicator.

DATA is read in a single transaction. The vendor firmware
(M5Unit-QRCode-Internal-FW ``Slave_Complete_Callback``) serves the decode
buffer from offset 0 for *any* register address in 0x1000..0x13FF and
resets its TX index on every register-address write, so reading in chunks
with an advancing address would return the first bytes again for every
chunk. Both vendor drivers (Arduino ``getDecodeData``, UiFlow
``get_qrcode_data``) read the whole LENGTH at 0x1000 in one go; so does
this driver (at most 512 bytes, far below the 8192-byte I2C_RDWR limit).

Verify on hardware (this driver was written without the device)
---------------------------------------------------------------

* **Single 512-byte read** -- the one-transaction DATA read above is what
  the vendor drivers do, but a full-length read has not been exercised on
  this Pi/kernel combination yet; RUNBOOK step 6b scans a payload longer
  than 32 bytes to check it.
* **Firmware-version register** -- the vendor I2C protocol sheet says
  0x00F0 while the Arduino library uses 0x00FE. This driver uses 0x00FE and
  only ever logs the value; nothing depends on it, and a NACK there is
  reported as ``unknown``/``unreadable`` rather than as a failure.

smbus2 is imported lazily so the module (and ``FakeScanner``) work on
machines without it. ``UnitQRCode`` accepts an injected bus object exposing
``i2c_rdwr(*msgs)`` together with a ``msg_factory`` that mimics
``smbus2.i2c_msg`` (``.write(addr, data)``, ``.read(addr, length)``;
after the transfer a read message yields its data via ``bytes(msg)``).
"""

from __future__ import annotations

from typing import Any, Protocol

DEFAULT_ADDR = 0x21
DATA_MAX = 512

REG_TRIGGER = 0x0000
REG_READY = 0x0010
REG_LENGTH = 0x0020
REG_TRIGGER_MODE = 0x0030
REG_TRIGGER_KEY = 0x0040
REG_FW_VERSION = 0x00FE
REG_DATA = 0x1000

READY_NONE = 0
READY_DATA = 1
READY_AGAIN = 2

TRIGGER_MODE_AUTO = 0
TRIGGER_MODE_MANUAL = 1


class ScannerError(Exception):
    """A logical scanner failure (bad length, mode read-back mismatch, ...).

    Low-level bus failures are *not* wrapped: ``OSError`` from the I2C bus
    propagates unchanged so the daemon can apply its retry policy.
    """


class Scanner(Protocol):
    """What the daemon needs from a scanner."""

    def ready(self) -> int: ...

    def read_payload(self) -> bytes: ...

    def clear(self) -> None: ...

    def set_trigger_mode(self, auto: bool) -> None: ...

    def firmware_version(self) -> int: ...


def _reg_bytes(reg: int) -> bytes:
    """Encode a 16-bit register address little-endian (low byte first)."""
    return bytes((reg & 0xFF, (reg >> 8) & 0xFF))


class UnitQRCode:
    """Register-level driver for the Unit QRCode over I2C.

    ``bus`` is either an I2C bus number (``smbus2.SMBus`` is opened lazily)
    or an already-open bus-like object with ``i2c_rdwr(*msgs)``.
    ``msg_factory`` builds messages; ``None`` means ``smbus2.i2c_msg``.
    """

    def __init__(
        self,
        bus: int | Any = 1,
        addr: int = DEFAULT_ADDR,
        msg_factory: Any | None = None,
    ) -> None:
        self.addr = addr
        self._owns_bus = isinstance(bus, int)
        if self._owns_bus or msg_factory is None:
            import smbus2  # lazy: only needed on the Pi

            if msg_factory is None:
                msg_factory = smbus2.i2c_msg
            if self._owns_bus:
                bus = smbus2.SMBus(bus)
        self._bus = bus
        self._msg = msg_factory

    # -- lifecycle ------------------------------------------------------------

    def close(self) -> None:
        """Close the bus if this object opened it."""
        if self._owns_bus and hasattr(self._bus, "close"):
            self._bus.close()

    def __enter__(self) -> "UnitQRCode":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- register primitives --------------------------------------------------

    def _write(self, reg: int, data: bytes) -> None:
        """One transaction with a single write message ``[lo, hi, *data]``."""
        self._bus.i2c_rdwr(self._msg.write(self.addr, _reg_bytes(reg) + bytes(data)))

    def _read(self, reg: int, n: int) -> bytes:
        """Write ``[lo, hi]`` then read ``n`` bytes in ONE transaction (repeated start)."""
        msg = self._msg.read(self.addr, n)
        self._bus.i2c_rdwr(self._msg.write(self.addr, _reg_bytes(reg)), msg)
        return bytes(msg)

    def _read_u8(self, reg: int) -> int:
        return self._read(reg, 1)[0]

    # -- public API -----------------------------------------------------------

    def ready(self) -> int:
        """READY register: 0 = nothing, 1 = data ready, 2 = data ready (two decodes since last read)."""
        return self._read_u8(REG_READY)

    def clear(self) -> None:
        """Write 0 to READY, discarding any pending payload."""
        self._write(REG_READY, bytes((READY_NONE,)))

    def get_trigger_mode(self) -> int:
        """TRIGGER_MODE register: 0 = auto, 1 = manual."""
        return self._read_u8(REG_TRIGGER_MODE)

    def set_trigger_mode(self, auto: bool) -> None:
        """Select auto (continuous) or manual scanning and verify by read-back."""
        want = TRIGGER_MODE_AUTO if auto else TRIGGER_MODE_MANUAL
        self._write(REG_TRIGGER_MODE, bytes((want,)))
        got = self.get_trigger_mode()
        if got != want:
            raise ScannerError(f"trigger mode read-back mismatch: wrote {want}, read {got}")

    def trigger(self, enable: bool) -> None:
        """Start (1) or stop (0) a scan; only meaningful in manual mode."""
        self._write(REG_TRIGGER, bytes((1 if enable else 0,)))

    def trigger_key(self) -> int:
        """TRIGGER_KEY register: 0 = button pressed, 1 = not pressed."""
        return self._read_u8(REG_TRIGGER_KEY)

    def firmware_version(self) -> int:
        """Firmware version byte (diagnostic only; register address unverified)."""
        return self._read_u8(REG_FW_VERSION)

    def read_payload(self) -> bytes:
        """Read the decoded payload and clear READY.

        Reads LENGTH (2 bytes LE), rejects 0 or > 512 (clearing READY so a
        bad frame does not wedge the loop), then reads all ``length`` DATA
        bytes in ONE transaction at 0x1000 (the firmware ignores the address
        offset within the DATA window, see the module docstring).
        """
        raw = self._read(REG_LENGTH, 2)
        length = int.from_bytes(raw, "little")
        if length == 0 or length > DATA_MAX:
            self.clear()
            raise ScannerError(f"invalid payload length {length} (expected 1..{DATA_MAX})")

        data = self._read(REG_DATA, length)
        self.clear()
        return data[:length]


class FakeScanner:
    """In-memory scanner for tests and ``WIFI_QR_FAKE_SCANNER`` dry runs.

    ``ready()`` pops from ``ready_sequence`` if given (0 once exhausted),
    otherwise returns 1 while payloads remain and 0 after. ``read_payload()``
    pops the next payload. Calls to ``set_trigger_mode`` and ``clear`` are
    recorded in ``trigger_mode_calls`` and ``cleared``.
    """

    def __init__(
        self,
        payloads: list[bytes] | None = None,
        ready_sequence: list[int] | None = None,
    ) -> None:
        self.payloads: list[bytes] = list(payloads or [])
        self.ready_sequence: list[int] | None = (
            list(ready_sequence) if ready_sequence is not None else None
        )
        self.trigger_mode_calls: list[bool] = []
        self.cleared = 0

    def ready(self) -> int:
        if self.ready_sequence is not None:
            return self.ready_sequence.pop(0) if self.ready_sequence else READY_NONE
        return READY_DATA if self.payloads else READY_NONE

    def read_payload(self) -> bytes:
        if not self.payloads:
            raise ScannerError("no payload available")
        return self.payloads.pop(0)

    def clear(self) -> None:
        self.cleared += 1

    def set_trigger_mode(self, auto: bool) -> None:
        self.trigger_mode_calls.append(auto)

    def firmware_version(self) -> int:
        return 0
