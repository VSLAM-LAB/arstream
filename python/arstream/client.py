"""Receives frames streamed by the ARStream iOS app (see FrameStreamer.swift for the format).

The ARS1 message is the one schema on every leg: phone -> Mac (this client), Mac -> subscribers
(publisher.py / subscriber.py, one ZeroMQ message per ARS1 message, bytes untouched) and the
recording's frames.jsonl (one header per line). A message whose header carries "event" (e.g.
{"event": "end"}, empty payload) is a control message, not a frame.
"""

import json
import socket
import struct
from dataclasses import dataclass, field
from functools import cached_property

import cv2
import numpy as np

from . import usbmux

DEFAULT_PORT = 5555
MAGIC = b"ARS1"
HEADER_STRUCT = struct.Struct("<4sII")  # magic, header_len, payload_len
END_EVENT = "end"

# ARKit cameras look down -z with y up; OpenCV cameras look down +z with y down.
ARKIT_TO_OPENCV = np.diag([1.0, -1.0, -1.0, 1.0])


@dataclass
class Frame:
    seq: int
    timestamp: float               # seconds, iPhone uptime clock
    K: np.ndarray                  # 3x3 intrinsics for `image`
    T_world_camera: np.ndarray     # 4x4 camera-to-world, OpenCV camera convention
    tracking: str                  # ARKit tracking state, "normal" when the pose is reliable
    jpeg: bytes = field(repr=False)
    header: dict = field(repr=False)

    @cached_property
    def image(self) -> np.ndarray:
        """BGR uint8 image (decoded on first access)."""
        return cv2.imdecode(np.frombuffer(self.jpeg, np.uint8), cv2.IMREAD_COLOR)

    @property
    def width(self) -> int:
        return self.header["width"]

    @property
    def height(self) -> int:
        return self.header["height"]


def encode_message(header: dict, payload: bytes = b"") -> bytes:
    header_bytes = json.dumps(header).encode()
    return HEADER_STRUCT.pack(MAGIC, len(header_bytes), len(payload)) + header_bytes + payload


def decode_message(raw: bytes) -> tuple[dict, bytes]:
    """(header, payload) of one ARS1 message held in memory."""
    if len(raw) < HEADER_STRUCT.size:
        raise ValueError(f"message of {len(raw)} bytes is shorter than the ARS1 prefix")
    magic, header_len, payload_len = HEADER_STRUCT.unpack_from(raw)
    if magic != MAGIC:
        raise ValueError(f"bad magic {magic!r}")
    start = HEADER_STRUCT.size
    if len(raw) != start + header_len + payload_len:
        raise ValueError(f"message of {len(raw)} bytes does not match its lengths ({header_len} + {payload_len})")
    header = json.loads(raw[start:start + header_len])
    return header, raw[start + header_len:]


def end_message() -> bytes:
    return encode_message({"event": END_EVENT})


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        k = sock.recv_into(view[got:])
        if k == 0:
            raise ConnectionError("stream closed by the phone")
        got += k
    return bytes(buf)


def parse_frame(header: dict, jpeg: bytes) -> Frame:
    T_arkit = np.array(header["T_world_camera"], dtype=np.float64).reshape(4, 4)
    return Frame(
        seq=header["seq"],
        timestamp=header["timestamp"],
        K=np.array(header["K"], dtype=np.float64).reshape(3, 3),
        T_world_camera=T_arkit @ ARKIT_TO_OPENCV,
        tracking=header["tracking"],
        jpeg=jpeg,
        header=header,
    )


class ARStreamClient:
    """Iterates over frames from the phone.

    with ARStreamClient.usb() as client:
        for frame in client:
            ...
    """

    def __init__(self, sock: socket.socket):
        self.sock = sock

    @classmethod
    def usb(cls, port: int = DEFAULT_PORT, serial: str | None = None) -> "ARStreamClient":
        return cls(usbmux.connect(port, serial))

    @classmethod
    def tcp(cls, host: str, port: int = DEFAULT_PORT) -> "ARStreamClient":
        sock = socket.create_connection((host, port), timeout=5)
        sock.settimeout(None)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return cls(sock)

    def read_message(self) -> bytes:
        """One ARS1 message as the phone sent it (prefix, header and payload)."""
        prefix = _recv_exact(self.sock, HEADER_STRUCT.size)
        magic, header_len, payload_len = HEADER_STRUCT.unpack(prefix)
        if magic != MAGIC:
            raise ValueError(f"bad magic {magic!r}, stream out of sync")
        return prefix + _recv_exact(self.sock, header_len + payload_len)

    def read_frame(self) -> Frame:
        header, jpeg = decode_message(self.read_message())
        return parse_frame(header, jpeg)

    def __iter__(self):
        while True:
            try:
                yield self.read_frame()
            except ConnectionError:
                return

    def messages(self):
        """Raw ARS1 messages until the phone closes the stream (for the relay: no decoding)."""
        while True:
            try:
                yield self.read_message()
            except ConnectionError:
                return

    def close(self):
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
