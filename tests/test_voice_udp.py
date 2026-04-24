"""Voice UDP transport tests.

Covers IP discovery response shape and RTP fan-out between clients in the
same voice room. Encryption is intentionally skipped — the server is a
passthrough SFU.
"""
from __future__ import annotations

import asyncio
import socket
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Windows's default ProactorEventLoop has thin UDP support in some asyncio
# versions; the selector loop is the documented-stable path for DatagramProtocol.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dapi_emu.state import WORLD
from dapi_emu.voice.udp_server import start_udp_server


def _ip_discovery_packet(ssrc: int) -> bytes:
    pkt = bytearray(74)
    struct.pack_into(">HH", pkt, 0, 0x0001, 0x0046)
    struct.pack_into(">I", pkt, 4, ssrc)
    return bytes(pkt)


def _rtp_packet(ssrc: int, seq: int = 1, payload: bytes = b"opus-fake") -> bytes:
    hdr = bytearray(12)
    hdr[0] = 0x80  # v2, no pad, no ext, cc=0
    hdr[1] = 0x78  # payload type 120 (opus)
    struct.pack_into(">H", hdr, 2, seq)
    struct.pack_into(">I", hdr, 4, 0)  # timestamp
    struct.pack_into(">I", hdr, 8, ssrc)
    return bytes(hdr) + payload


async def _recv_with_timeout(sock: socket.socket, timeout: float = 1.0) -> bytes:
    """Read one datagram using the running loop, with a timeout."""
    loop = asyncio.get_running_loop()
    sock.setblocking(False)
    fut: asyncio.Future[bytes] = loop.create_future()

    def _on_readable() -> None:
        if fut.done():
            return
        try:
            data, _ = sock.recvfrom(4096)
        except BlockingIOError:
            return
        fut.set_result(data)

    loop.add_reader(sock.fileno(), _on_readable)
    try:
        return await asyncio.wait_for(fut, timeout)
    finally:
        loop.remove_reader(sock.fileno())


def test_ip_discovery_response() -> None:
    async def run() -> None:
        protocol, port = await start_udp_server("127.0.0.1", 0)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("127.0.0.1", 0))
            try:
                sock.sendto(_ip_discovery_packet(0xDEADBEEF), ("127.0.0.1", port))
                data = await _recv_with_timeout(sock, 1.0)
                assert len(data) == 74, f"expected 74-byte reply, got {len(data)}"
                t, ln = struct.unpack(">HH", data[:4])
                assert t == 0x0002
                assert ln == 0x0046
                ssrc = struct.unpack(">I", data[4:8])[0]
                assert ssrc == 0xDEADBEEF
                ip_str = data[8:72].split(b"\x00", 1)[0].decode("ascii")
                assert ip_str == "127.0.0.1"
                reported_port = struct.unpack(">H", data[72:74])[0]
                assert reported_port == sock.getsockname()[1]
            finally:
                sock.close()
        finally:
            protocol.transport.close()

    asyncio.run(run())


def test_rtp_forwarded_between_peers() -> None:
    async def run() -> None:
        # Clean slate for voice_sessions so we fully control the room map.
        WORLD.voice_sessions.clear()
        protocol, port = await start_udp_server("127.0.0.1", 0)
        try:
            ssrc_a = 0x11111111
            ssrc_b = 0x22222222
            room = {"guild_id": "g-1", "channel_id": "c-1"}
            WORLD.voice_sessions["sess-a"] = {
                "ssrc": ssrc_a, "user_id": "ua", **room, "remote_addr": None,
            }
            WORLD.voice_sessions["sess-b"] = {
                "ssrc": ssrc_b, "user_id": "ub", **room, "remote_addr": None,
            }
            # Also register a third bot in a *different* room to prove isolation.
            WORLD.voice_sessions["sess-c"] = {
                "ssrc": 0x33333333, "user_id": "uc",
                "guild_id": "g-2", "channel_id": "c-2", "remote_addr": None,
            }

            sock_a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock_a.bind(("127.0.0.1", 0))
            sock_b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock_b.bind(("127.0.0.1", 0))
            sock_c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock_c.bind(("127.0.0.1", 0))

            try:
                # Each client does IP discovery so the server learns its addr.
                for sock, ssrc in ((sock_a, ssrc_a), (sock_b, ssrc_b), (sock_c, 0x33333333)):
                    sock.sendto(_ip_discovery_packet(ssrc), ("127.0.0.1", port))
                    await _recv_with_timeout(sock, 1.0)

                # A sends RTP — B should receive it, C should not.
                payload = b"hello-from-a"
                pkt = _rtp_packet(ssrc_a, seq=7, payload=payload)
                sock_a.sendto(pkt, ("127.0.0.1", port))

                got_b = await _recv_with_timeout(sock_b, 1.0)
                assert got_b == pkt, "B should get A's packet byte-for-byte"

                # Confirm C does not see it.
                with pytest.raises(asyncio.TimeoutError):
                    await _recv_with_timeout(sock_c, 0.3)

                # Now B replies — A should receive it.
                pkt_b = _rtp_packet(ssrc_b, seq=8, payload=b"hello-from-b")
                sock_b.sendto(pkt_b, ("127.0.0.1", port))
                got_a = await _recv_with_timeout(sock_a, 1.0)
                assert got_a == pkt_b

                # And A should not get its own packet echoed back.
                with pytest.raises(asyncio.TimeoutError):
                    await _recv_with_timeout(sock_a, 0.3)
            finally:
                sock_a.close()
                sock_b.close()
                sock_c.close()
        finally:
            protocol.transport.close()
            WORLD.voice_sessions.clear()

    asyncio.run(run())


def test_rtp_ignored_for_unknown_ssrc() -> None:
    """Packets from SSRCs that aren't in any voice_session must not crash."""
    async def run() -> None:
        WORLD.voice_sessions.clear()
        protocol, port = await start_udp_server("127.0.0.1", 0)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("127.0.0.1", 0))
            try:
                sock.sendto(_rtp_packet(0xABCD1234), ("127.0.0.1", port))
                # Nothing should come back.
                with pytest.raises(asyncio.TimeoutError):
                    await _recv_with_timeout(sock, 0.3)
            finally:
                sock.close()
        finally:
            protocol.transport.close()

    asyncio.run(run())
