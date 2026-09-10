from __future__ import annotations

import unittest
from unittest.mock import patch

from earnings_v25.providers import FinancialCompanyClient


class FinancialCompanySourceTests(unittest.TestCase):
    def test_passes_github_public_data_key_only_to_the_protected_proxy(self) -> None:
        client = FinancialCompanyClient(
            "https://example.supabase.co",
            "service-role-key",
            "internal-token",
            "data-go-key",
            session=object(),
        )
        with patch("earnings_v25.providers.bounded_request", return_value={
            "status": "no_report", "crno": "1234567890123",
        }) as request:
            self.assertEqual(client.quarter_financials("1234567890123", 2018, 3), [])
        self.assertEqual(
            request.call_args.kwargs["headers"]["X-Public-Data-API-Key"],
            "data-go-key",
        )
        self.assertEqual(
            request.call_args.kwargs["json"],
            {"crno": "1234567890123", "fiscal_year": 2018},
        )


if __name__ == "__main__":
    unittest.main()
