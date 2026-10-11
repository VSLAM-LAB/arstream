"""Minimal usbmuxd client: open a TCP connection to a port on a USB-connected iPhone.

macOS runs usbmuxd as part of the system, so no extra tools (iproxy, libimobiledevice)
are needed. Protocol: 16-byte little-endian header (length, version=1, type=8 (plist), tag)
followed by an XML plist; after a successful "Connect" the socket becomes a raw tunnel.
"""

import plistlib
import socket
import struct

USBMUXD_SOCKET = "/var/run/usbmuxd"

_RESULT_MESSAGES = {
    2: "bad device",
    3: "connection refused (is the ARStream app open on the phone?)",
    6: "bad version",
}


class UsbmuxError(RuntimeError):
    pass


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("usbmuxd closed the connection")
        buf += chunk
    return bytes(buf)


def _open() -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(USBMUXD_SOCKET)
    return sock


def _request(sock: socket.socket, message: dict) -> dict:
    message = {"ClientVersionString": "arstream", "ProgName": "arstream", **message}
    body = plistlib.dumps(message)
    sock.sendall(struct.pack("<IIII", 16 + len(body), 1, 8, 1) + body)
    length, _version, _type, _tag = struct.unpack("<IIII", _recv_exact(sock, 16))
    return plistlib.loads(_recv_exact(sock, length - 16))


def list_devices(usb_only: bool = True) -> list[dict]:
    """Returns usbmuxd device entries: {"DeviceID": int, "Properties": {...}}."""
    with _open() as sock:
        reply = _request(sock, {"MessageType": "ListDevices"})
    devices = reply.get("DeviceList", [])
    if usb_only:
        devices = [d for d in devices if d["Properties"].get("ConnectionType") == "USB"]
    return devices


def connect(port: int, serial: str | None = None) -> socket.socket:
    """Connects to `port` on the phone. Picks the only USB device unless `serial` is given."""
    devices = list_devices()
    if serial is not None:
        devices = [d for d in devices if d["Properties"].get("SerialNumber") == serial]
    if not devices:
        raise UsbmuxError("no iPhone found over USB (plugged in, unlocked and trusted?)")
    if len(devices) > 1:
        serials = [d["Properties"].get("SerialNumber") for d in devices]
        raise UsbmuxError(f"several devices connected, pick one with --serial: {serials}")

    sock = _open()
    reply = _request(sock, {
        "MessageType": "Connect",
        "DeviceID": devices[0]["DeviceID"],
        "PortNumber": socket.htons(port),
    })
    result = reply.get("Number", -1)
    if result != 0:
        sock.close()
        raise UsbmuxError(f"usbmuxd connect failed: {_RESULT_MESSAGES.get(result, result)}")
    return sock
