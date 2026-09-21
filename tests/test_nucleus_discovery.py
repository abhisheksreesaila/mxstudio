import sys
import types
import unittest
from unittest.mock import patch

from notebooks.mxstudio.discovery import NucleusDiscovery


class DiscoveryTests(unittest.TestCase):
    def test_missing_dependency_is_safe(self):
        with patch.dict(sys.modules, {"zeroconf": None}):
            self.assertFalse(NucleusDiscovery("nucleus", address="192.0.2.4").start())

    def test_registration_failure_closes(self):
        class FakeZc:
            def register_service(self, _service):
                raise OSError("multicast unavailable")

            def close(self):
                self.closed = True

        class FakeInfo:
            def __init__(self, *args, **kwargs):
                self.args, self.kwargs = args, kwargs

        fake = types.SimpleNamespace(ServiceInfo=FakeInfo, Zeroconf=FakeZc)
        discovery = NucleusDiscovery("nucleus", address="192.0.2.4")
        with patch.dict(sys.modules, {"zeroconf": fake}):
            self.assertFalse(discovery.start())
        self.assertIsNone(discovery._zeroconf)
        self.assertIsNone(discovery._service)

    def test_register_and_cleanup(self):
        class FakeZc:
            def register_service(self, service):
                self.service = service

            def unregister_service(self, service):
                self.unregistered = service

            def close(self):
                self.closed = True

        class FakeInfo:
            def __init__(self, *args, **kwargs):
                self.args, self.kwargs = args, kwargs

        fake = types.SimpleNamespace(ServiceInfo=FakeInfo, Zeroconf=FakeZc)
        discovery = NucleusDiscovery("nucleus.local", address="192.0.2.4")
        with patch.dict(sys.modules, {"zeroconf": fake}):
            self.assertTrue(discovery.start())
            self.assertEqual(discovery._service.kwargs["properties"][b"v"], b"1")
            self.assertEqual(discovery._service.kwargs["server"], "nucleus.local.")
            discovery.stop()
        self.assertIsNone(discovery._zeroconf)
