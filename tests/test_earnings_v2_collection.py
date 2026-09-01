from decimal import Decimal
import unittest

from earnings_v2.dart_financials import DartFinancialCollector, extract_quarter
from earnings_v2.open_dart import OpenDartV2Client, OpenDartV2Error


def account(
    corp_code: str,
    name: str,
    amount: str,
    *,
    scope: str = "CFS",
    account_id: str = "",
    cumulative: str | None = None,
    order: int = 1,
) -> dict[str, str]:
    return {
        "corp_code": corp_code,
        "account_nm": name,
        "account_id": account_id,
        "thstrm_amount": amount,
        "thstrm_add_amount": cumulative if cumulative is not None else amount,
        "fs_div": scope,
        "sj_div": "IS",
        "rcept_no": "20260515000001",
        "currency": "KRW",
        "ord": str(order),
    }


def complete_rows(corp_code: str, *, scope: str = "CFS", amount: str = "100") -> list[dict[str, str]]:
    return [
        account(corp_code, "매출액", amount, scope=scope, account_id="ifrs-full_Revenue", order=1),
        account(corp_code, "영업이익", amount, scope=scope, account_id="dart_OperatingIncomeLoss", order=2),
        account(corp_code, "당기순이익", amount, scope=scope, account_id="ifrs-full_ProfitLoss", order=3),
    ]


class EarningsV2OpenDartTransportTests(unittest.TestCase):
    def test_multi_company_transport_uses_one_official_request(self):
        class Response:
            status_code = 200

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"status": "000", "list": []}

        class Session:
            def __init__(self):
                self.calls = []

            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return Response()

        session = Session()
        client = OpenDartV2Client("test", interval=0, session=session)
        client.multi_accounts(["00000001", "00000002"], 2026, 1)
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(session.calls[0][0].endswith("/fnlttMultiAcnt.json"))
        self.assertEqual(session.calls[0][1]["params"]["corp_code"], "00000001,00000002")

    def test_multi_company_transport_rejects_more_than_one_hundred(self):
        client = object.__new__(OpenDartV2Client)
        with self.assertRaises(ValueError):
            client.multi_accounts([f"{value:08d}" for value in range(101)], 2026, 1)

    def test_transport_failure_never_exposes_api_key(self):
        secret = "a-secret-that-must-not-appear"

        class Session:
            @staticmethod
            def get(*_args, **_kwargs):
                raise RuntimeError(f"failed URL crtfc_key={secret}")

        client = OpenDartV2Client(secret, interval=0, session=Session())
        with self.assertRaises(OpenDartV2Error) as captured:
            client.multi_accounts(["00000001"], 2026, 1)
        self.assertNotIn(secret, str(captured.exception))

    def test_full_statement_rows_inherit_requested_scope(self):
        class Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"status": "000", "list": [{"account_nm": "영업수익"}]}

        class Session:
            @staticmethod
            def get(*_args, **_kwargs):
                return Response()

        client = OpenDartV2Client("test", interval=0, session=Session())
        rows = client.all_accounts("00000001", 2026, 3, "CFS")
        self.assertEqual(rows[0]["fs_div"], "CFS")


class EarningsV2DartFinancialTests(unittest.TestCase):
    def test_complete_batch_rows_need_no_single_company_fallback(self):
        class Client:
            def __init__(self):
                self.full_calls = []
                self.batch_calls = []

            def multi_accounts(self, codes, _year, quarter):
                self.batch_calls.append((tuple(codes), quarter))
                return [row for code in codes for row in complete_rows(code)]

            def all_accounts(self, *args):
                self.full_calls.append(args)
                return []

        client = Client()
        result = DartFinancialCollector(client).collect(["00000001", "00000002"], 2026, 1)
        self.assertEqual(set(result.values), {"00000001", "00000002"})
        self.assertEqual(result.errors, {})
        self.assertEqual(client.batch_calls, [(('00000001', '00000002'), 1)])
        self.assertEqual(client.full_calls, [])

    def test_q2_reported_three_month_values_do_not_request_q1(self):
        class Client:
            def __init__(self):
                self.batch_calls = []

            def multi_accounts(self, codes, _year, quarter):
                self.batch_calls.append((tuple(codes), quarter))
                return [row for code in codes for row in complete_rows(code)]

            @staticmethod
            def all_accounts(*_args):
                raise AssertionError("complete Q2 rows must not use full statements")

        client = Client()
        result = DartFinancialCollector(client).collect(["00000001", "00000002"], 2026, 2)
        self.assertEqual(set(result.values), {"00000001", "00000002"})
        self.assertEqual(client.batch_calls, [(('00000001', '00000002'), 2)])

    def test_q4_requests_q3_once_for_annual_subtraction(self):
        class Client:
            def __init__(self):
                self.batch_calls = []

            def multi_accounts(self, codes, _year, quarter):
                self.batch_calls.append((tuple(codes), quarter))
                amount = "140" if quarter == 4 else "90"
                return [row for code in codes for row in complete_rows(code, amount=amount)]

            @staticmethod
            def all_accounts(*_args):
                raise AssertionError("complete Q4 rows must not use full statements")

        client = Client()
        result = DartFinancialCollector(client).collect(["00000001", "00000002"], 2026, 4)
        self.assertEqual(set(result.values), {"00000001", "00000002"})
        self.assertEqual(
            client.batch_calls,
            [(('00000001', '00000002'), 4), (('00000001', '00000002'), 3)],
        )

    def test_cfs_and_ofs_are_never_mixed(self):
        rows = [
            account("00000001", "매출액", "100", scope="CFS"),
            account("00000001", "영업이익", "20", scope="CFS"),
            *complete_rows("00000001", scope="OFS", amount="50"),
        ]
        value, diagnostic = extract_quarter(rows, [], 1)
        self.assertEqual(diagnostic, "")
        self.assertEqual(value.scope, "OFS")
        self.assertEqual(value.net_income, Decimal("50"))

    def test_q4_uses_annual_minus_q3_cumulative(self):
        current = complete_rows("00000001", amount="140")
        previous = complete_rows("00000001", amount="90")
        value, diagnostic = extract_quarter(current, previous, 4)
        self.assertEqual(diagnostic, "")
        self.assertEqual(value.top_line, Decimal("50"))
        self.assertEqual(value.operating_income, Decimal("50"))
        self.assertEqual(value.net_income, Decimal("50"))

    def test_q2_uses_q1_cumulative_when_three_month_amount_is_absent(self):
        current = complete_rows("00000001", amount="")
        previous = complete_rows("00000001", amount="40")
        for row in current:
            row["thstrm_add_amount"] = "100"
        value, diagnostic = extract_quarter(current, previous, 2)
        self.assertEqual(diagnostic, "")
        self.assertEqual(value.operating_income, Decimal("60"))

    def test_explicit_financial_total_is_selected_without_summing_income_leaves(self):
        rows = [
            account("00000001", "영업수익", "100", account_id="custom_Total", order=1),
            account("00000001", "이자수익", "70", account_id="custom_Interest", order=2),
            account("00000001", "수수료수익", "30", account_id="custom_Fee", order=3),
            account("00000001", "영업이익", "20", account_id="dart_OperatingIncomeLoss", order=4),
            account("00000001", "당기순이익", "10", account_id="ifrs-full_ProfitLoss", order=5),
        ]
        value, diagnostic = extract_quarter(rows, [], 1)
        self.assertEqual(diagnostic, "")
        self.assertEqual(value.top_line, Decimal("100"))

    def test_sales_revenue_name_combinations_are_explicit_top_lines(self):
        for name in ("매출", "매출액", "수익", "수익/매출액", "매출(수익)"):
            with self.subTest(name=name):
                rows = [
                    account("00000001", name, "100", account_id="custom_Revenue", order=1),
                    account("00000001", "영업이익", "20", account_id="dart_OperatingIncomeLoss", order=2),
                    account("00000001", "당기순이익", "10", account_id="ifrs-full_ProfitLoss", order=3),
                ]
                value, diagnostic = extract_quarter(rows, [], 1)
                self.assertEqual(diagnostic, "")
                self.assertEqual(value.top_line, Decimal("100"))

    def test_financial_top_line_exact_names_are_supported(self):
        for name in ("순영업이익", "순영업수익", "영업수익"):
            with self.subTest(name=name):
                rows = [
                    account("00000001", name, "100", account_id="custom_Total", order=1),
                    account("00000001", "영업이익", "20", account_id="dart_OperatingIncomeLoss", order=2),
                    account("00000001", "당기순이익", "10", account_id="ifrs-full_ProfitLoss", order=3),
                ]
                value, diagnostic = extract_quarter(rows, [], 1)
                self.assertEqual(diagnostic, "")
                self.assertEqual(value.top_line, Decimal("100"))

    def test_income_leaves_are_never_summed_into_top_line(self):
        rows = [
            account("00000001", "금융수익", "70", account_id="custom_Finance", order=1),
            account("00000001", "이자수익", "30", account_id="custom_Interest", order=2),
            account("00000001", "영업이익", "20", account_id="dart_OperatingIncomeLoss", order=3),
            account("00000001", "당기순이익", "10", account_id="ifrs-full_ProfitLoss", order=4),
        ]
        value, diagnostic = extract_quarter(rows, [], 1)
        self.assertIsNotNone(value)
        self.assertIsNone(value.top_line)
        self.assertEqual(value.operating_income, Decimal("20"))
        self.assertEqual(value.net_income, Decimal("10"))
        self.assertIn("top_line=no", diagnostic)

    def test_unrelated_labels_do_not_override_operating_or_net_income(self):
        rows = [
            account("00000001", "매출액", "100", account_id="ifrs-full_Revenue", order=1),
            account("00000001", "영업비용", "20", account_id="dart_OperatingIncomeLoss", order=2),
            account("00000001", "법인세차감전순이익", "10", account_id="ifrs-full_ProfitLoss", order=3),
        ]
        value, diagnostic = extract_quarter(rows, [], 1)
        self.assertIsNotNone(value)
        self.assertEqual(value.top_line, Decimal("100"))
        self.assertIsNone(value.operating_income)
        self.assertIsNone(value.net_income)
        self.assertIn("op=no", diagnostic)
        self.assertIn("net=no", diagnostic)

    def test_only_unresolved_company_uses_full_statement_fallback(self):
        class Client:
            def __init__(self):
                self.full_calls = []

            @staticmethod
            def multi_accounts(codes, _year, _quarter):
                return complete_rows(codes[0])

            def all_accounts(self, code, _year, _quarter, scope):
                self.full_calls.append((code, scope))
                return complete_rows(code, scope=scope) if scope == "CFS" else []

        client = Client()
        result = DartFinancialCollector(client).collect(["00000001", "00000002"], 2026, 1)
        self.assertEqual(set(result.values), {"00000001", "00000002"})
        self.assertEqual(client.full_calls, [("00000002", "CFS")])

    def test_partial_batch_values_are_preserved_and_only_current_scope_is_fetched(self):
        class Client:
            def __init__(self):
                self.full_calls = []

            @staticmethod
            def multi_accounts(codes, _year, _quarter):
                return [
                    account(codes[0], "영업이익", "20", account_id="dart_OperatingIncomeLoss"),
                    account(codes[0], "당기순이익", "10", account_id="ifrs-full_ProfitLoss"),
                ]

            def all_accounts(self, code, _year, quarter, scope):
                self.full_calls.append((code, quarter, scope))
                return [
                    account(code, "영업수익", "100", scope=scope),
                    # These deliberately differ: valid batch values must win.
                    account(code, "영업이익", "999", scope=scope),
                    account(code, "당기순이익", "999", scope=scope),
                ]

        client = Client()
        result = DartFinancialCollector(client).collect(["00000001"], 2026, 3)
        value = result.values["00000001"]
        self.assertTrue(value.complete)
        self.assertEqual(value.top_line, Decimal("100"))
        self.assertEqual(value.operating_income, Decimal("20"))
        self.assertEqual(value.net_income, Decimal("10"))
        self.assertEqual(client.full_calls, [("00000001", 3, "CFS")])

    def test_unresolved_partial_is_returned_instead_of_discarded(self):
        class Client:
            @staticmethod
            def multi_accounts(codes, _year, _quarter):
                return [
                    account(codes[0], "영업이익", "20", account_id="dart_OperatingIncomeLoss"),
                    account(codes[0], "당기순이익", "10", account_id="ifrs-full_ProfitLoss"),
                ]

            @staticmethod
            def all_accounts(*_args):
                return []

        result = DartFinancialCollector(Client()).collect(["00000001"], 2026, 1)
        value = result.values["00000001"]
        self.assertFalse(value.complete)
        self.assertIsNone(value.top_line)
        self.assertEqual(value.operating_income, Decimal("20"))
        self.assertEqual(value.net_income, Decimal("10"))
        self.assertIn("00000001", result.errors)


if __name__ == "__main__":
    unittest.main()
