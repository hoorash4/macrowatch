import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import liquidity_pipeline as lp


class LiquidityTests(unittest.TestCase):
    def test_percentile_ties_and_direction(self):
        self.assertEqual(lp.percentile(1, [1, 1]), 50)
        self.assertEqual(lp.percentile(0, [1, 2]), 0)
        self.assertEqual(lp.percentile(3, [1, 2]), 100)

    def test_no_future_or_stale_asof(self):
        values = {date(2021, 9, 1): 1, date(2021, 9, 10): 10}
        self.assertEqual(lp.asof(values, date(2021, 9, 3), 7), 1)
        self.assertIsNone(lp.asof(values, date(2021, 9, 9), 7))
        self.assertIsNone(lp.asof(values, date(2021, 8, 31), 7))

    def test_us_unit_conversion_and_zero_rrp(self):
        day = date(2021, 9, 8)
        data = dict(sofr={}, effr={}, p25={}, p75={}, iorb={},
                    reserves={day: 4000000, day-timedelta(days=28): 2000000},
                    assets={day: 20000}, rrp={day: 0})
        result = lp.features('US', data)['capacity'][day]
        self.assertEqual(result['reserve_ratio'], 20)
        self.assertEqual(result['reserve_change'], 100)
        self.assertEqual(result['rrp_ratio'], 0)

    def test_korea_flow_not_net_buys_or_double_counted(self):
        months = [date(2021, m, 1) for m in (6, 7, 8, 9)]
        data = dict(base={}, call={}, kofr={}, m2={d: 100+i for i,d in enumerate(months)},
                    lf={d: 200+i for i,d in enumerate(months)},
                    equity_flow={d: -1 for d in months}, bond_flow={d: 2 for d in months})
        result = lp.features('KR', data)['capacity'][months[-1]]
        self.assertEqual(result['equity_flow'], -3)
        self.assertEqual(result['bond_flow'], 6)
        self.assertAlmostEqual(result['m2_change'], 3)
        del data['bond_flow'][months[1]]
        self.assertNotIn(months[-1], lp.features('KR', data)['capacity'])

    def test_snapshot_exact_headers_and_null(self):
        payload = {'data': {'chart_opt': {'data': {'csv': 'period,기준금리,콜금리(익일물)\n1630454400000,0,\n1630540800000,1,2'}}}}
        rows = lp.parse_snapshot(payload, lp.SNAPSHOTS[849], date(2021,1,1), date(2022,1,1))
        self.assertEqual(rows['base'][date(2021,9,1)], 0)
        self.assertNotIn(date(2021,9,1), rows['call'])
        with self.assertRaises(RuntimeError):
            lp.parse_snapshot(payload, {'wrong': 'bad'}, date(2021,1,1), date(2022,1,1))

    def test_weights_and_calendar(self):
        for weights in lp.WEIGHTS.values():
            self.assertAlmostEqual(sum(weights.values()), 1)
        self.assertEqual(lp.shift_month(date(2024,2,29), -12), date(2023,2,28))

    def test_future_does_not_change_past_score(self):
        from unittest.mock import patch
        start = lp.START
        feature = {'pressure': {}, 'capacity': {}}
        for metric, rows in feature.items():
            for i in range(80):
                rows[start+timedelta(days=i)] = {key: i for key in lp.WEIGHTS['US', metric]}
        with patch.object(lp, 'features', return_value=feature):
            before = lp.calculate('US', {})
            for metric in feature:
                feature[metric][start+timedelta(days=81)] = {key: 100000 for key in lp.WEIGHTS['US', metric]}
            after = lp.calculate('US', {})
        self.assertEqual(before, [r for r in after if r['observation_date'] <= (start+timedelta(days=79)).isoformat()])


if __name__ == '__main__':
    unittest.main()
