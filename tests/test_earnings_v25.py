from __future__ import annotations

import unittest

from earnings_v25.diagnose_structured import parser, relevant_accounts


class EarningsV25DiagnosticTests(unittest.TestCase):
    def test_requires_supported_quarter(self) -> None:
        args = parser().parse_args(["--year", "2016", "--quarter", "1"])
        self.assertEqual((args.year, args.quarter), (2016, 1))

    def test_reports_historical_account_names_and_ignores_balance_sheet(self) -> None:
        rows = [
            {"sj_div": "IS", "account_id": "custom_OperatingProfit", "account_nm": "영업이익(손실)", "thstrm_amount": "10"},
            {"sj_div": "CIS", "account_id": "custom_ParentProfit", "account_nm": "지배기업소유주지분순이익", "thstrm_amount": "8"},
            {"sj_div": "BS", "account_id": "ifrs-full_Equity", "account_nm": "자본총계", "thstrm_amount": "20"},
        ]

        result = relevant_accounts(rows)

        self.assertEqual([row["account_nm"] for row in result], [
            "영업이익(손실)", "지배기업소유주지분순이익",
        ])


if __name__ == "__main__":
    unittest.main()
