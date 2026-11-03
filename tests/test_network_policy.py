from __future__ import annotations

import socket
import unittest

from max_grounding.errors import FetchError
from max_grounding.network_policy import (
    is_admissible_result_url,
    resolve_public_addresses,
)


class ResultUrlAdmissionTests(unittest.TestCase):
    def test_accepts_public_http_and_https_targets(self) -> None:
        self.assertTrue(is_admissible_result_url("https://example.com/article"))
        self.assertTrue(is_admissible_result_url("http://93.184.216.34/path"))

    def test_rejects_credentials_and_non_http_schemes(self) -> None:
        self.assertFalse(is_admissible_result_url("https://user:pass@example.com/x"))
        self.assertFalse(is_admissible_result_url("file:///etc/passwd"))
        self.assertFalse(is_admissible_result_url("javascript:alert(1)"))

    def test_rejects_localhost_names(self) -> None:
        self.assertFalse(is_admissible_result_url("http://localhost/admin"))
        self.assertFalse(is_admissible_result_url("http://api.localhost/admin"))

    def test_rejects_literal_non_public_ip_targets(self) -> None:
        blocked = [
            "http://127.0.0.1/",
            "http://[::1]/",
            "http://[::ffff:127.0.0.1]/",
            "http://127.1/",
            "http://2130706433/",
            "http://10.0.0.5/",
            "http://172.16.0.5/",
            "http://192.168.1.5/",
            "http://169.254.169.254/latest/meta-data/",
            "http://0.0.0.0/",
            "http://224.0.0.1/",
            "http://192.0.2.1/",
        ]
        for url in blocked:
            with self.subTest(url=url):
                self.assertFalse(is_admissible_result_url(url))

    def test_rejects_malformed_ports(self) -> None:
        self.assertFalse(is_admissible_result_url("https://example.com:99999/x"))

    def test_rejects_ascii_control_characters_anywhere_in_url(self) -> None:
        for codepoint in (0x00, 0x1F, 0x7F):
            character = chr(codepoint)
            with self.subTest(codepoint=codepoint):
                self.assertFalse(
                    is_admissible_result_url(
                        "https://example.com/path" + character + "suffix"
                    )
                )


class ConnectionTargetResolutionTests(unittest.TestCase):
    @staticmethod
    def _resolver(addresses: list[str]):
        def resolve_addresses(host: str, port: int, *args: object, **kwargs: object):
            rows = []
            for address in addresses:
                family = socket.AF_INET6 if ":" in address else socket.AF_INET
                sockaddr = (address, port, 0, 0) if family == socket.AF_INET6 else (address, port)
                rows.append(
                    (
                        family,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        sockaddr,
                    )
                )
            return rows

        return resolve_addresses

    def test_accepts_and_deduplicates_only_public_dns_answers(self) -> None:
        addresses = resolve_public_addresses(
            "example.com",
            443,
            resolver=self._resolver(
                ["93.184.216.34", "93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]
            ),
        )
        self.assertEqual(
            addresses,
            ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"),
        )

    def test_rejects_mixed_public_and_private_dns_answers(self) -> None:
        with self.assertRaises(FetchError):
            resolve_public_addresses(
                "rebind.example",
                443,
                resolver=self._resolver(["93.184.216.34", "127.0.0.1"]),
            )

    def test_rejects_private_link_local_reserved_multicast_and_empty_answers(self) -> None:
        blocked_sets = [
            ["10.0.0.1"],
            ["169.254.169.254"],
            ["192.0.2.1"],
            ["224.0.0.1"],
            ["::1"],
            ["fe80::1"],
            [],
        ]
        for addresses in blocked_sets:
            with self.subTest(addresses=addresses):
                with self.assertRaises(FetchError):
                    resolve_public_addresses(
                        "unsafe.example",
                        443,
                        resolver=self._resolver(addresses),
                    )


if __name__ == "__main__":
    unittest.main()
