"""Tests for wifi_qr.scanner.

The I2C bus is replaced by ``FakeBus``, which records every message that
passes through ``i2c_rdwr`` and serves reads from a sparse byte map, so the
register encoding and chunked read behaviour can be asserted without
hardware or smbus2.
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

from wifi_qr.scanner import (
    DATA_MAX,
    REG_DATA,
    REG_FW_VERSION,
    REG_LENGTH,
    REG_READY,
    REG_TRIGGER,
    REG_TRIGGER_MODE,
    FakeScanner,
    ScannerError,
    UnitQRCode,
)

ADDR = 0x21


class FakeMsg:
    """Stand-in for ``smbus2.i2c_msg`` (write/read constructors, bytes()/list())."""

    def __init__(self, kind: str, addr: int, buf: bytes, length: int) -> None:
        self.kind = kind
        self.addr = addr
        self.buf = bytearray(buf)
        self.length = length

    @classmethod
    def write(cls, addr: int, data) -> "FakeMsg":
        data = bytes(data)
        return cls("w", addr, data, len(data))

    @classmethod
    def read(cls, addr: int, length: int) -> "FakeMsg":
        return cls("r", addr, b"", length)

    def __bytes__(self) -> bytes:
        return bytes(self.buf)

    def __iter__(self):
        return iter(self.buf)

    def __len__(self) -> int:
        return self.length


class FakeBus:
    """Records i2c_rdwr calls and serves reads from a sparse register map.

    ``transactions`` is a list with one entry per ``i2c_rdwr`` call; each
    entry is a list of ``(addr, kind, bytes)`` tuples, one per message.
    Writes update the map unless ``apply_writes`` is False, so a register
    read-back after a write sees the written value by default.
    """

    def __init__(self, regs: dict[int, bytes] | None = None, *, apply_writes: bool = True) -> None:
        self.mem: dict[int, int] = {}
        for base, data in (regs or {}).items():
            for i, b in enumerate(data):
                self.mem[base + i] = b
        self.apply_writes = apply_writes
        self.transactions: list[list[tuple[int, str, bytes]]] = []

    @property
    def messages(self) -> list[tuple[int, str, bytes]]:
        return [m for txn in self.transactions for m in txn]

    def i2c_rdwr(self, *msgs: FakeMsg) -> None:
        txn: list[tuple[int, str, bytes]] = []
        reg: int | None = None
        for msg in msgs:
            if msg.kind == "w":
                payload = bytes(msg.buf)
                assert len(payload) >= 2, "register address must be 2 bytes"
                reg = payload[0] | (payload[1] << 8)
                if self.apply_writes:
                    for i, b in enumerate(payload[2:]):
                        self.mem[reg + i] = b
                txn.append((msg.addr, "w", payload))
            else:
                assert reg is not None, "read must follow a register write in the same call"
                data = bytes(self.mem.get(reg + i, 0) for i in range(msg.length))
                msg.buf = bytearray(data)
                txn.append((msg.addr, "r", data))
        self.transactions.append(txn)


class RaisingBus(FakeBus):
    def i2c_rdwr(self, *msgs: FakeMsg) -> None:
        raise OSError(121, "Remote I/O error")


def make(regs: dict[int, bytes] | None = None, **kw) -> tuple[UnitQRCode, FakeBus]:
    bus = FakeBus(regs, apply_writes=kw.pop("apply_writes", True))
    dev = UnitQRCode(bus=bus, addr=ADDR, msg_factory=FakeMsg, **kw)
    return dev, bus


# --- register encoding ------------------------------------------------------


def test_ready_reads_register_little_endian_in_one_transaction() -> None:
    dev, bus = make({REG_READY: b"\x01"})
    assert dev.ready() == 1
    assert bus.transactions == [[(ADDR, "w", b"\x10\x00"), (ADDR, "r", b"\x01")]]


def test_firmware_version_uses_0x00fe_high_byte_second() -> None:
    dev, bus = make({REG_FW_VERSION: b"\x2a"})
    assert dev.firmware_version() == 0x2A
    assert bus.transactions == [[(ADDR, "w", b"\xfe\x00"), (ADDR, "r", b"\x2a")]]


def test_clear_writes_zero_to_ready_in_single_write_message() -> None:
    dev, bus = make({REG_READY: b"\x01"})
    dev.clear()
    assert bus.transactions == [[(ADDR, "w", b"\x10\x00\x00")]]
    assert bus.mem[REG_READY] == 0


def test_get_trigger_mode() -> None:
    dev, bus = make({REG_TRIGGER_MODE: b"\x01"})
    assert dev.get_trigger_mode() == 1
    assert bus.transactions == [[(ADDR, "w", b"\x30\x00"), (ADDR, "r", b"\x01")]]


@pytest.mark.parametrize("auto, value", [(True, 0), (False, 1)])
def test_set_trigger_mode_writes_then_reads_back(auto: bool, value: int) -> None:
    dev, bus = make({REG_TRIGGER_MODE: bytes([1 - value])})
    dev.set_trigger_mode(auto)
    assert bus.transactions == [
        [(ADDR, "w", bytes([0x30, 0x00, value]))],
        [(ADDR, "w", b"\x30\x00"), (ADDR, "r", bytes([value]))],
    ]


def test_set_trigger_mode_readback_mismatch_raises() -> None:
    dev, _ = make({REG_TRIGGER_MODE: b"\x01"}, apply_writes=False)
    with pytest.raises(ScannerError):
        dev.set_trigger_mode(auto=True)


@pytest.mark.parametrize("enable, value", [(True, 1), (False, 0)])
def test_trigger_writes_trigger_register(enable: bool, value: int) -> None:
    dev, bus = make()
    dev.trigger(enable)
    assert bus.transactions == [[(ADDR, "w", bytes([0x00, 0x00, value]))]]
    assert REG_TRIGGER == 0x0000


def test_custom_address_is_used_for_every_message() -> None:
    bus = FakeBus({REG_READY: b"\x00"})
    dev = UnitQRCode(bus=bus, addr=0x42, msg_factory=FakeMsg)
    dev.ready()
    assert {m[0] for m in bus.messages} == {0x42}


# --- read_payload ------------------------------------------------------------


def test_read_payload_reads_length_then_chunks_with_advancing_register() -> None:
    data = bytes(range(70))
    dev, bus = make({REG_LENGTH: (70).to_bytes(2, "little"), REG_DATA: data}, chunk=32)

    assert dev.read_payload() == data

    assert bus.transactions == [
        [(ADDR, "w", b"\x20\x00"), (ADDR, "r", b"\x46\x00")],
        [(ADDR, "w", b"\x00\x10"), (ADDR, "r", data[0:32])],
        [(ADDR, "w", b"\x20\x10"), (ADDR, "r", data[32:64])],
        [(ADDR, "w", b"\x40\x10"), (ADDR, "r", data[64:70])],
        [(ADDR, "w", b"\x10\x00\x00")],
    ]


def test_read_payload_exact_chunk_multiple_has_no_empty_read() -> None:
    data = bytes(range(64))
    dev, bus = make({REG_LENGTH: b"\x40\x00", REG_DATA: data}, chunk=32)
    assert dev.read_payload() == data
    reads = [m for m in bus.messages if m[1] == "r"]
    assert [len(m[2]) for m in reads] == [2, 32, 32]


def test_read_payload_length_is_little_endian() -> None:
    data = bytes(300)
    dev, bus = make({REG_LENGTH: b"\x2c\x01", REG_DATA: data}, chunk=100)
    assert len(dev.read_payload()) == 300
    reads = [m for m in bus.messages if m[1] == "r"]
    assert [len(m[2]) for m in reads] == [2, 100, 100, 100]


def test_read_payload_max_length_512_is_accepted() -> None:
    data = bytes(i & 0xFF for i in range(DATA_MAX))
    dev, _ = make({REG_LENGTH: b"\x00\x02", REG_DATA: data}, chunk=32)
    assert dev.read_payload() == data


@pytest.mark.parametrize("length", [b"\x00\x00", b"\x01\x02", b"\xff\xff"])
def test_read_payload_bad_length_raises_and_clears(length: bytes) -> None:
    dev, bus = make({REG_LENGTH: length, REG_READY: b"\x01"})
    with pytest.raises(ScannerError):
        dev.read_payload()
    assert bus.transactions[-1] == [(ADDR, "w", b"\x10\x00\x00")]
    assert not any(m[2][:2] == b"\x00\x10" for m in bus.messages if m[1] == "w")


def test_oserror_from_bus_propagates_unchanged() -> None:
    dev = UnitQRCode(bus=RaisingBus(), addr=ADDR, msg_factory=FakeMsg)
    with pytest.raises(OSError) as exc:
        dev.ready()
    assert exc.value.errno == 121
    with pytest.raises(OSError):
        dev.read_payload()


@pytest.mark.parametrize("chunk", [0, -1])
def test_invalid_chunk_rejected(chunk: int) -> None:
    with pytest.raises(ValueError):
        UnitQRCode(bus=FakeBus(), msg_factory=FakeMsg, chunk=chunk)


# --- smbus2 laziness ----------------------------------------------------------


def test_module_imports_without_smbus2() -> None:
    code = (
        "import sys; sys.modules['smbus2'] = None; "
        "import wifi_qr.scanner as s; s.FakeScanner(); print('ok')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "ok"


def test_int_bus_opens_smbus_lazily_and_uses_i2c_msg(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[int] = []

    class SMBus:
        def __init__(self, bus: int) -> None:
            opened.append(bus)
            self.inner = FakeBus({REG_READY: b"\x02"})

        def i2c_rdwr(self, *msgs) -> None:
            self.inner.i2c_rdwr(*msgs)

        def close(self) -> None:
            opened.append(-1)

    fake = types.ModuleType("smbus2")
    fake.SMBus = SMBus  # type: ignore[attr-defined]
    fake.i2c_msg = FakeMsg  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "smbus2", fake)

    dev = UnitQRCode(bus=1)
    assert opened == [1]
    assert dev.ready() == 2
    dev.close()
    assert opened == [1, -1]


# --- FakeScanner --------------------------------------------------------------


def test_fake_scanner_ready_follows_payloads() -> None:
    fs = FakeScanner(payloads=[b"a", b"b"])
    assert fs.ready() == 1
    assert fs.read_payload() == b"a"
    assert fs.ready() == 1
    assert fs.read_payload() == b"b"
    assert fs.ready() == 0


def test_fake_scanner_ready_sequence_overrides() -> None:
    fs = FakeScanner(payloads=[b"x"], ready_sequence=[0, 2, 1])
    assert [fs.ready(), fs.ready(), fs.ready()] == [0, 2, 1]
    assert fs.ready() == 0


def test_fake_scanner_records_calls() -> None:
    fs = FakeScanner()
    fs.set_trigger_mode(True)
    fs.set_trigger_mode(False)
    fs.clear()
    fs.clear()
    assert fs.trigger_mode_calls == [True, False]
    assert fs.cleared == 2
    assert fs.firmware_version() == 0
    assert fs.ready() == 0


def test_fake_scanner_read_without_payload_raises() -> None:
    with pytest.raises(ScannerError):
        FakeScanner().read_payload()
