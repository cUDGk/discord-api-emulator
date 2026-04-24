"""Voice UDP transport (simplified SFU).

Real Discord voice is SRTP over UDP. Bots do IP discovery first (74-byte
packet starting with 0x0001), then stream RTP frames with a 12-byte header +
encrypted opus payload. This server:

  * answers IP discovery so discord.py's voice client thinks NAT worked,
  * binds the SSRC it sees in discovery to the remote (ip, port) it came from,
  * forwards every subsequent RTP packet to the other SSRCs bound to the same
    voice "room" (guild_id, channel_id), looked up via WORLD.voice_sessions.

No crypto. The opus payload stays encrypted — listeners just get the bytes as
the sender produced them, which is what a passthrough SFU is supposed to do.
"""
from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any

from ..state import WORLD

log = logging.getLogger("dapi.voice.udp")


class VoiceUDPServer(asyncio.DatagramProtocol):
    """Receives UDP from voice clients, answers IP discovery, fans out RTP."""

    transport: asyncio.DatagramTransport

    def __init__(self) -> None:
        # ssrc -> remote addr (ip, port). Populated by IP discovery.
        self.ssrc_to_addr: dict[int, tuple[str, int]] = {}
        # remote addr -> ssrc. Reverse lookup for teardown / rebinds.
        self.addr_to_ssrc: dict[tuple[str, int], int] = {}

    # --- asyncio protocol hooks ------------------------------------------
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        # asyncio narrows this to DatagramTransport at runtime.
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        # IP Discovery: 74 bytes, type == 0x0001 (request), length == 0x0046.
        # Response uses type 0x0002 with the same length. See Discord voice docs.
        if len(data) == 74 and data[:2] == b"\x00\x01":
            ssrc = struct.unpack(">I", data[4:8])[0]
            self._bind_ssrc(ssrc, addr)
            resp = bytearray(74)
            struct.pack_into(">HH", resp, 0, 0x0002, 0x0046)
            struct.pack_into(">I", resp, 4, ssrc)
            ip_bytes = addr[0].encode("ascii")[:63]
            resp[8:8 + len(ip_bytes)] = ip_bytes
            # bytes 8..71 is null-terminated IP string; byte 72..73 is port.
            struct.pack_into(">H", resp, 72, addr[1])
            self.transport.sendto(bytes(resp), addr)
            log.debug("udp: ip discovery ssrc=%d -> %s:%d", ssrc, addr[0], addr[1])
            return

        # RTP: first byte version/flags == 0x80 (v2, no pad, no ext) or 0x90
        # (v2 with extension). Either way the high two bits are 10.
        if len(data) >= 12 and (data[0] & 0xC0) == 0x80:
            ssrc = struct.unpack(">I", data[8:12])[0]
            self._forward_rtp(data, ssrc, addr)
            return

        log.debug("udp: ignoring %d-byte packet from %s", len(data), addr)

    # --- SSRC binding ----------------------------------------------------
    def _bind_ssrc(self, ssrc: int, addr: tuple[str, int]) -> None:
        """Associate an SSRC with the remote UDP endpoint it arrived from.

        Also stamps the matching voice_sessions entry with the remote addr so
        other parts of the stack can see who's actually sending audio.
        """
        prev_addr = self.ssrc_to_addr.get(ssrc)
        if prev_addr is not None and prev_addr != addr:
            self.addr_to_ssrc.pop(prev_addr, None)
        self.ssrc_to_addr[ssrc] = addr
        self.addr_to_ssrc[addr] = ssrc

        for sess in WORLD.voice_sessions.values():
            if sess.get("ssrc") == ssrc:
                sess["remote_addr"] = addr
                break

    def _forward_rtp(self, data: bytes, from_ssrc: int, addr: tuple[str, int]) -> None:
        """Send `data` to every other client in the same voice room."""
        # Refresh binding in case discovery was skipped (some clients do that).
        if self.ssrc_to_addr.get(from_ssrc) != addr:
            self._bind_ssrc(from_ssrc, addr)

        source = self._session_for_ssrc(from_ssrc)
        if source is None:
            return
        room = (source.get("guild_id"), source.get("channel_id"))
        if room == (None, None):
            return

        for sess in WORLD.voice_sessions.values():
            if sess.get("ssrc") == from_ssrc:
                continue
            if (sess.get("guild_id"), sess.get("channel_id")) != room:
                continue
            dest = sess.get("remote_addr")
            if dest is None:
                continue
            self.transport.sendto(data, dest)

    def _session_for_ssrc(self, ssrc: int) -> dict[str, Any] | None:
        for sess in WORLD.voice_sessions.values():
            if sess.get("ssrc") == ssrc:
                return sess
        return None

    def unbind_ssrc(self, ssrc: int) -> None:
        addr = self.ssrc_to_addr.pop(ssrc, None)
        if addr is not None:
            self.addr_to_ssrc.pop(addr, None)


async def start_udp_server(
    host: str = "127.0.0.1", port: int = 0,
) -> tuple[VoiceUDPServer, int]:
    """Bind a UDP socket and return (protocol, actual_port).

    `port=0` asks the OS for an ephemeral port; the real one is read back from
    the socket so the gateway can advertise it in the voice READY payload.
    """
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        VoiceUDPServer, local_addr=(host, port),
    )
    bound_port = transport.get_extra_info("socket").getsockname()[1]
    assert isinstance(protocol, VoiceUDPServer)
    return protocol, bound_port
