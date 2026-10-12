"""Subscriber to a relay (publisher.py): iterates over frames until the end-of-stream message."""

import zmq

from .client import END_EVENT, Frame, decode_message, parse_frame
from .publisher import DEFAULT_URL


class Subscriber:
    """Frames from `arstream publish`.

    with Subscriber() as sub:
        for frame in sub:          # ends at the publisher's end-of-stream message
            ...

    conflate=True (default) keeps only the newest frame: a subscriber slower than the stream skips
    frames instead of falling behind. conflate=False queues up to `queue` frames, then drops the oldest.
    """

    def __init__(self, url: str = DEFAULT_URL, conflate: bool = True, queue: int = 30, poll_ms: int = 100):
        self.context = zmq.Context.instance()
        self.socket = self.context.socket(zmq.SUB)
        if conflate:
            self.socket.setsockopt(zmq.CONFLATE, 1)
        else:
            self.socket.setsockopt(zmq.RCVHWM, queue)
        self.socket.setsockopt(zmq.LINGER, 0)
        self.socket.subscribe(b"")
        self.socket.connect(url)
        self.url = url
        self.poll_ms = poll_ms
        self.ended = False

    def read_message(self) -> bytes | None:
        """Next raw ARS1 message, or None once the end-of-stream message arrived. Blocks, but
        wakes up every poll_ms so Ctrl-C is handled."""
        if self.ended:
            return None
        while True:
            if self.socket.poll(self.poll_ms, zmq.POLLIN):
                message = self.socket.recv(copy=True)
                header, _ = decode_message(message)
                if header.get("event") == END_EVENT:
                    self.ended = True
                    return None
                return message

    def read_frame(self) -> Frame | None:
        message = self.read_message()
        if message is None:
            return None
        header, jpeg = decode_message(message)
        return parse_frame(header, jpeg)

    def __iter__(self):
        while (frame := self.read_frame()) is not None:
            yield frame

    def close(self):
        self.socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
