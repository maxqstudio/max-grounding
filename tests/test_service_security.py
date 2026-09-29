from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from max_grounding.api import create_rest_app
from max_grounding.mcp_server import create_mcp_server


API_KEY = "s" * 32


class Phase12ServiceSecurityTests(unittest.TestCase):
    def test_parent_auth_middleware_protects_mounted_mcp_endpoint(self) -> None:
        service = MagicMock()
        mcp = create_mcp_server(service)
        app = create_rest_app(
            service,
            api_key=API_KEY,
            mcp_server=mcp,
            allowed_hosts=("testserver",),
            allowed_origins=(),
        )
        with TestClient(app) as client:
            response = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "server/discover"},
            )
        self.assertEqual(response.status_code, 401)

    def test_no_debug_traceback_is_returned_for_unknown_route(self) -> None:
        service = MagicMock()
        app = create_rest_app(
            service,
            api_key=API_KEY,
            allowed_hosts=("testserver",),
            allowed_origins=(),
        )
        response = TestClient(app).get(
            "/does-not-exist",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Traceback", response.text)


if __name__ == "__main__":
    unittest.main()
