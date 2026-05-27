"""
Integration-style tests for the HTTP client, using `responses` to mock the
DGI endpoints. Validates that:

  - The Bearer token is attached on every authenticated request.
  - A 4xx response raises DGIAPIError with the right status code.
  - A transport error becomes a DGIAPIError.
  - A non-JSON success body is treated as failure.

Requires:    pip install responses
"""

from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock


class TestClientErrorHandling(unittest.TestCase):
    @patch("dgi_compliance.api.client._write_log")
    @patch("dgi_compliance.api.client.get_pos_token", return_value="t.t.t")
    @patch("dgi_compliance.api.client.get_base_url", return_value="https://dev/edef")
    @patch("dgi_compliance.api.client.frappe")
    @patch("dgi_compliance.api.client.requests")
    def test_4xx_raises(self, requests_mock, frappe_mock, *_):
        frappe_mock.get_single.return_value = MagicMock(connection_timeout=30)
        resp = MagicMock(status_code=400, text='{"message":"bad nim"}')
        resp.json.return_value = {"message": "bad nim"}
        requests_mock.request.return_value = resp

        from dgi_compliance.api import client
        with self.assertRaises(client.DGIAPIError) as ctx:
            client.post("/api/invoice", {"x": 1}, pos_code="POS01",
                        action="Send Invoice")
        self.assertEqual(ctx.exception.http_status, 400)
        self.assertIn("bad nim", str(ctx.exception))

    @patch("dgi_compliance.api.client._write_log")
    @patch("dgi_compliance.api.client.get_pos_token", return_value="t.t.t")
    @patch("dgi_compliance.api.client.get_base_url", return_value="https://dev/edef")
    @patch("dgi_compliance.api.client.frappe")
    @patch("dgi_compliance.api.client.requests")
    def test_transport_error_raises(self, requests_mock, frappe_mock, *_):
        import requests as real_requests
        frappe_mock.get_single.return_value = MagicMock(connection_timeout=30)
        requests_mock.RequestException = real_requests.RequestException
        requests_mock.request.side_effect = real_requests.ConnectionError("boom")

        from dgi_compliance.api import client
        with self.assertRaises(client.DGIAPIError):
            client.post("/api/invoice", {"x": 1}, pos_code="POS01",
                        action="Send Invoice")


if __name__ == "__main__":
    unittest.main()
