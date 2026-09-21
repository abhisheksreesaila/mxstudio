"""Optional mDNS advertisement for the Nucleus inference endpoint."""
from __future__ import annotations

import socket
from typing import Optional


class NucleusDiscovery:
    def __init__(
        self,
        hostname: str,
        port: int = 443,
        name: str = "MX Studio Nucleus",
        address: Optional[str] = None,
    ) -> None:
        self.hostname, self.port, self.name, self.address = hostname, port, name, address
        self._zeroconf = None
        self._service = None

    def start(self) -> bool:
        """Advertise when zeroconf is installed; otherwise return a clear fallback."""
        try:
            from zeroconf import ServiceInfo, Zeroconf

            selected = self.address or socket.gethostbyname(self.hostname)
            ip = socket.inet_aton(selected)
            service_type = "_mxstudio._tcp.local."
            if self.hostname.endswith(".local."):
                server = self.hostname
            elif self.hostname.endswith(".local"):
                server = f"{self.hostname}."
            else:
                server = f"{self.hostname.rstrip('.')}.local."
            self._service = ServiceInfo(
                service_type,
                f"{self.name}.{service_type}",
                addresses=[ip],
                port=self.port,
                properties={b"v": b"1", b"path": b"/v1"},
                server=server,
            )
            self._zeroconf = Zeroconf()
            try:
                self._zeroconf.register_service(self._service)
            except Exception:
                self._zeroconf.close()
                self._zeroconf = self._service = None
                return False
            return True
        except (ImportError, OSError, ValueError, socket.gaierror):
            self._zeroconf = None
            self._service = None
            return False

    def stop(self) -> None:
        if self._zeroconf:
            try:
                if self._service:
                    self._zeroconf.unregister_service(self._service)
            finally:
                self._zeroconf.close()
        self._zeroconf = self._service = None
