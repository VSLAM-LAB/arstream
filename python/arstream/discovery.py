"""Finds ARStream phones on the local network via Bonjour (mDNS)."""

import socket
import time
from dataclasses import dataclass

from zeroconf import IPVersion, ServiceBrowser, ServiceListener, Zeroconf

SERVICE_TYPE = "_arstream._tcp.local."


@dataclass
class Service:
    name: str
    host: str
    port: int


def _lan_address() -> str | None:
    """IPv4 address of the interface with the default route (no packets are sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))  # TEST-NET address: only used to pick a route
            return sock.getsockname()[0]
    except OSError:
        return None


class _Collector(ServiceListener):
    def __init__(self):
        self.services: dict[str, Service] = {}

    def add_service(self, zc: Zeroconf, type_: str, name: str):
        info = zc.get_service_info(type_, name, timeout=2000)
        if info is None:
            return
        ipv4 = info.parsed_addresses(IPVersion.V4Only)
        if ipv4:
            self.services[name] = Service(name.removesuffix("." + type_), ipv4[0], info.port)

    def update_service(self, zc: Zeroconf, type_: str, name: str):
        self.add_service(zc, type_, name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str):
        self.services.pop(name, None)


def discover(timeout: float = 3.0) -> list[Service]:
    """Browses for up to `timeout` seconds and returns the phones found.

    Only the Mac's LAN (Wi-Fi) interface is used: when the phone is also plugged in, it would
    otherwise answer over the USB network link too, with an address that is not on Wi-Fi.
    """
    lan = _lan_address()
    zc = Zeroconf(interfaces=[lan]) if lan else Zeroconf()
    collector = _Collector()
    try:
        ServiceBrowser(zc, SERVICE_TYPE, collector)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not collector.services:
            time.sleep(0.1)
        time.sleep(0.3)  # give other phones a moment to answer too
        return list(collector.services.values())
    finally:
        zc.close()
