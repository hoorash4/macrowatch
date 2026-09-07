import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import liquidity_pipeline as lp


def weekly_environment(start, end, value_for_index):
    output, cursor, index = {}, start, 0
    while cursor <= end:
        output[cursor] = {key: value_for_index(index, direction)
                          for key, direction in lp.US_DIRECTIONS.items()}
        cursor += timedelta(weeks=1)
        index += 1
    return {'environment': output}


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

    def test_us_net_supply_units_and_rrp_release(self):
        day = date(2021, 9, 8)
        before = day - timedelta(days=91)
        data = dict(sofr={day: .1}, iorb={day: .15}, ioer={},
                    fed_assets={before: 8000000, day: 8000000},
                    tga={before: 1000000, day: 1000000}, rrp={before: 1000, day: 0},
                    real_yield={day: -1}, credit_conditions={day: -.1})
        result = lp.features('US', data)['environment'][day]
        self.assertAlmostEqual(result['net_supply_change'], (7000000 / 6000000 - 1) * 100)
        self.assertAlmostEqual(result['funding_spread'], -.05)
        data['rrp'][day] = 1000
        self.assertEqual(lp.features('US', data)['environment'][day]['net_supply_change'], 0)

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
        feature = weekly_environment(date(2016, 1, 1), date(2021, 12, 31),
                                     lambda index, _direction: index)
        with patch.object(lp, 'features', return_value=feature):
            before = lp.calculate('US', {}, date(2022, 1, 3))
            feature['environment'][date(2022, 1, 7)] = {key: 100000 for key in lp.US_DIRECTIONS}
            after = lp.calculate('US', {}, date(2022, 1, 10))
        self.assertEqual(before, [r for r in after if r['observation_date'] <= '2021-12-31'])

    def test_environment_direction_and_unchanged_momentum(self):
        from unittest.mock import patch
        feature = weekly_environment(date(2016, 1, 1), date(2021, 12, 31),
                                     lambda _index, _direction: 1)
        with patch.object(lp, 'features', return_value=feature):
            flat = lp.calculate('US', {}, date(2022, 1, 3))
            self.assertTrue(all(row['score'] == 50 for row in flat))
            feature['environment'][date(2021, 12, 31)] = {
                key: 1 + direction for key, direction in lp.US_DIRECTIONS.items()
            }
            improved = lp.calculate('US', {}, date(2022, 1, 3))
            self.assertTrue(all(row['score'] > 50 for row in improved
                                if row['observation_date'] == '2021-12-31'))
            del feature['environment'][date(2021, 12, 10)]
            with self.assertRaises(RuntimeError):
                lp.calculate('US', {}, date(2022, 1, 3))

    def test_weekly_four_week_average_excludes_open_week(self):
        values = {
            date(2026, 7, 10): {'spread': 1}, date(2026, 7, 17): {'spread': 2},
            date(2026, 7, 24): {'spread': 3}, date(2026, 7, 31): {'spread': 4},
            date(2026, 8, 3): {'spread': 100},
        }
        self.assertEqual(lp.weekly_smoothed_features(values, date(2026, 8, 3)),
                         {date(2026, 7, 31): {'spread': 2.5}})

    def test_monthly_average_before_ranking_and_open_month_excluded(self):
        values = {date(2026, 7, 1): {'spread': 0}, date(2026, 7, 31): {'spread': 10},
                  date(2026, 8, 1): {'spread': 100}}
        self.assertEqual(lp.monthly_features(values, date(2026, 8, 15)),
                         {date(2026, 7, 1): {'spread': 5}})


if __name__ == '__main__':
    unittest.main()

