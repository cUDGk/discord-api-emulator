"""Gateway payload encoding (json / etf) and stream compression (zlib / zstd).

Discord clients negotiate `encoding=json|etf` and `compress=zlib-stream|zstd-stream`
on the WebSocket query string. This module handles both axes so `server.py` can
stay thin.

ETF support: prefers the `erlpack` C extension when installed, otherwise falls
back to a pure-python encoder/decoder that covers the subset Discord actually
sends/receives (dicts, lists, strings, ints, floats, booleans, None, nesting).
"""
from __future__ import annotations

import json
import struct
from typing import Any

try:
    import erlpack  # type: ignore[import-not-found]
    _HAS_ERLPACK = True
except ImportError:
    erlpack = None  # type: ignore[assignment]
    _HAS_ERLPACK = False


# ---------------------------------------------------------------------------
# ETF (External Term Format) — minimal subset
# ---------------------------------------------------------------------------
# https://www.erlang.org/doc/apps/erts/erl_ext_dist.html

_ETF_VERSION = 131
_TAG_NEW_FLOAT = 70           # 8-byte big-endian IEEE-754 double
_TAG_SMALL_INTEGER = 97       # 1-byte unsigned int (0-255)
_TAG_INTEGER = 98             # 4-byte big-endian signed int
_TAG_SMALL_TUPLE = 104        # 1-byte arity, then arity terms
_TAG_NIL = 106                # empty list
_TAG_BINARY = 109             # 4-byte length, then bytes (we use UTF-8)
_TAG_LIST = 108               # 4-byte length, terms, then NIL tail
_TAG_SMALL_ATOM_UTF8 = 119    # 1-byte length, then UTF-8 bytes


def _etf_encode_term(obj: Any, out: bytearray) -> None:
    if obj is None:
        _etf_encode_atom("nil", out)
    elif obj is True:
        _etf_encode_atom("true", out)
    elif obj is False:
        _etf_encode_atom("false", out)
    elif isinstance(obj, int):
        if 0 <= obj <= 0xFF:
            out.append(_TAG_SMALL_INTEGER)
            out.append(obj)
        elif -(2**31) <= obj <= (2**31 - 1):
            out.append(_TAG_INTEGER)
            out.extend(struct.pack(">i", obj))
        else:
            # Out of int32 range — represent as binary string. Discord rarely
            # hits this for snowflakes (it sends them as strings already).
            _etf_encode_term(str(obj), out)
    elif isinstance(obj, float):
        out.append(_TAG_NEW_FLOAT)
        out.extend(struct.pack(">d", obj))
    elif isinstance(obj, str):
        data = obj.encode("utf-8")
        out.append(_TAG_BINARY)
        out.extend(struct.pack(">I", len(data)))
        out.extend(data)
    elif isinstance(obj, (bytes, bytearray)):
        out.append(_TAG_BINARY)
        out.extend(struct.pack(">I", len(obj)))
        out.extend(obj)
    elif isinstance(obj, (list, tuple)):
        if not obj:
            out.append(_TAG_NIL)
            return
        out.append(_TAG_LIST)
        out.extend(struct.pack(">I", len(obj)))
        for item in obj:
            _etf_encode_term(item, out)
        out.append(_TAG_NIL)  # proper-list tail
    elif isinstance(obj, dict):
        out.append(116)  # MAP_EXT
        out.extend(struct.pack(">I", len(obj)))
        for k, v in obj.items():
            _etf_encode_term(k, out)
            _etf_encode_term(v, out)
    else:
        # Last resort — coerce via JSON round-trip so we never silently drop data.
        _etf_encode_term(json.loads(json.dumps(obj, default=str)), out)


def _etf_encode_atom(name: str, out: bytearray) -> None:
    data = name.encode("utf-8")
    out.append(_TAG_SMALL_ATOM_UTF8)
    out.append(len(data))
    out.extend(data)


def etf_pack(obj: Any) -> bytes:
    if _HAS_ERLPACK:
        return erlpack.pack(obj)  # type: ignore[union-attr]
    out = bytearray([_ETF_VERSION])
    _etf_encode_term(obj, out)
    return bytes(out)


def etf_unpack(data: bytes) -> Any:
    if _HAS_ERLPACK:
        return erlpack.unpack(data)  # type: ignore[union-attr]
    if not data or data[0] != _ETF_VERSION:
        raise ValueError("ETF payload missing version marker 131")
    value, idx = _etf_decode_term(data, 1)
    if idx != len(data):
        raise ValueError(f"ETF trailing bytes at {idx}/{len(data)}")
    return value


def _etf_decode_term(buf: bytes, idx: int) -> tuple[Any, int]:
    tag = buf[idx]; idx += 1
    if tag == _TAG_SMALL_INTEGER:
        return buf[idx], idx + 1
    if tag == _TAG_INTEGER:
        (val,) = struct.unpack(">i", buf[idx:idx + 4])
        return val, idx + 4
    if tag == _TAG_NEW_FLOAT:
        (val,) = struct.unpack(">d", buf[idx:idx + 8])
        return val, idx + 8
    if tag == _TAG_SMALL_ATOM_UTF8:
        ln = buf[idx]; idx += 1
        name = buf[idx:idx + ln].decode("utf-8")
        idx += ln
        return _atom_to_python(name), idx
    if tag == 118:  # ATOM_UTF8_EXT (defensive — produced by erlpack sometimes)
        (ln,) = struct.unpack(">H", buf[idx:idx + 2]); idx += 2
        name = buf[idx:idx + ln].decode("utf-8")
        idx += ln
        return _atom_to_python(name), idx
    if tag == _TAG_BINARY:
        (ln,) = struct.unpack(">I", buf[idx:idx + 4]); idx += 4
        val = buf[idx:idx + ln].decode("utf-8")
        return val, idx + ln
    if tag == _TAG_NIL:
        return [], idx
    if tag == _TAG_LIST:
        (ln,) = struct.unpack(">I", buf[idx:idx + 4]); idx += 4
        items = []
        for _ in range(ln):
            v, idx = _etf_decode_term(buf, idx)
            items.append(v)
        # Skip the proper-list tail (NIL in well-formed payloads).
        if idx < len(buf) and buf[idx] == _TAG_NIL:
            idx += 1
        return items, idx
    if tag == 116:  # MAP_EXT
        (ln,) = struct.unpack(">I", buf[idx:idx + 4]); idx += 4
        out: dict[Any, Any] = {}
        for _ in range(ln):
            k, idx = _etf_decode_term(buf, idx)
            v, idx = _etf_decode_term(buf, idx)
            out[k] = v
        return out, idx
    if tag == _TAG_SMALL_TUPLE:
        arity = buf[idx]; idx += 1
        items = []
        for _ in range(arity):
            v, idx = _etf_decode_term(buf, idx)
            items.append(v)
        return tuple(items), idx
    raise ValueError(f"ETF: unsupported tag {tag} at offset {idx-1}")


def _atom_to_python(name: str) -> Any:
    if name == "true":
        return True
    if name == "false":
        return False
    if name == "nil" or name == "null":
        return None
    return name


# ---------------------------------------------------------------------------
# encode / decode façade
# ---------------------------------------------------------------------------

def encode(obj: Any, encoding: str) -> bytes | str:
    """Serialize `obj` for the given gateway encoding."""
    if encoding == "etf":
        return etf_pack(obj)
    return json.dumps(obj, separators=(",", ":"))


def decode(data: bytes | str, encoding: str) -> Any:
    """Deserialize an inbound frame for the given gateway encoding."""
    if encoding == "etf":
        if isinstance(data, str):
            data = data.encode("latin-1")
        return etf_unpack(data)
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    return json.loads(data)


# ---------------------------------------------------------------------------
# Stream compression
# ---------------------------------------------------------------------------

class Compressor:
    """Per-session payload compressor.

    `zlib-stream`: shared deflate context, flushed with Z_SYNC_FLUSH after each
        payload — that's the marker (00 00 ff ff) discord.js looks for.
    `zstd-stream`: each payload is an independent zstd frame. Discord's real
        gateway uses a streaming dictionary; for emulation an independent frame
        per send is what `zstandard.ZstdDecompressor.decompress` accepts and is
        sufficient for the libraries we care about.
    """

    def __init__(self, kind: str | None) -> None:
        self.kind = kind
        self._z = None
        self._cctx = None
        if kind == "zlib-stream":
            import zlib
            self._z = zlib.compressobj(level=6, wbits=15)
        elif kind == "zstd-stream":
            try:
                import zstandard
                self._cctx = zstandard.ZstdCompressor(level=3)
            except ImportError:
                # Library absent — fall back to no compression rather than fail
                # the whole connection. Clients that explicitly asked for zstd
                # would still expect binary frames, but we'd rather negotiate
                # than 4000-close a real client.
                self.kind = None

    def compress(self, data: bytes) -> bytes:
        if self.kind == "zlib-stream":
            import zlib
            assert self._z is not None
            return self._z.compress(data) + self._z.flush(zlib.Z_SYNC_FLUSH)
        if self.kind == "zstd-stream":
            assert self._cctx is not None
            return self._cctx.compress(data)
        return data
