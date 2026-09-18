from __future__ import annotations

import unittest
from datetime import date, timedelta

from historical_pivot_rule_backfill import normalize_points, structure


def rows(values, start="2020-01-01", step_days=14):
    base = date.fromisoformat(start)
    return [{"date": (base + timedelta(days=i * step_days)).isoformat(), "value": float(v)} for i,v in enumerate(values)]


class HistoricalPivotRuleEngineTests(unittest.TestCase):
    def analyze(self, values):
        source=rows(values)
        points,_,_=normalize_points(source,source[0]["date"],source[-1]["date"])
        return structure(points)

    def test_exact_extreme_is_used(self):
        values=[10,20,40,80,100,75,50,30,20,25,30]
        result=self.analyze(values)
        self.assertTrue(any(p["value"]==100 for p in result["pivots"]))

    def test_small_visual_reversal_is_rejected(self):
        # Big rise, tiny 2%-ish pullback on fixed Y, then continuation.
        values=[0,20,40,60,80,100,98,99,97,96,80,60,40]
        result=self.analyze(values)
        dates_values={(p["date"],p["value"]) for p in result["pivots"] if p["grade"]=="A"}
        self.assertFalse(any(v in {98,99,97} for _,v in dates_values))

    def test_large_amplitude_directionless_box_can_be_sideways(self):
        values=[20,80,18,95,22,70,19,84,21,76,20,82]
        result=self.analyze(values)
        self.assertTrue(result["sideways_boundaries"])


    def test_repeated_large_oscillations_are_box_not_spikes(self):
        values=[10,90,12,88,11,92,14,86,13,89,12,91,15,85]
        result=self.analyze(values)
        self.assertTrue(result["sideways_boundaries"])
        self.assertFalse(any(p["type"]=="spike_extreme" for p in result["pivots"]))

    def test_straight_flat_long_box_is_sideways(self):
        values=[50,30,15,10,10.2,9.9,10.1,10,10.2,9.8,10,10.1,10,20,40,70,95]
        result=self.analyze(values)
        self.assertTrue(any(b["mode"]=="flat" for b in result["sideways_boundaries"]))


    def test_broad_down_up_down_sequence_is_not_one_box(self):
        values=[90,82,74,66,58,52,48,50,47,45,48,54,60,68,76,82,78,72,66,60,54,48]
        result=self.analyze(values)
        self.assertFalse(any(
            b["start_date"]==rows(values)[0]["date"] and b["end_date"]==rows(values)[-1]["date"]
            for b in result["sideways_boundaries"]
        ))

    def test_hh_hl_continuation_is_merged(self):
        values=[10,30,20,45,32,60,48,75,65,90]
        result=self.analyze(values)
        ordinary=[p for p in result["pivots"] if p["type"]=="major_reversal"]
        self.assertLessEqual(len(ordinary),1)

    def test_spike_requires_half_y_axis_and_keeps_triplet(self):
        values=[20,18,15,10,90,12,14,13,12,11,10,9,8,7,6,5,4,3,4,6,9,14,20,30,45,60]
        result=self.analyze(values)
        types={p["type"] for p in result["pivots"]}
        self.assertTrue({"spike_entry","spike_extreme","spike_retracement"}.issubset(types))

    def test_sub_half_axis_excursion_is_not_spike(self):
        values=[20,18,15,10,45,12,14,13,12,11,10,9,8,7,6,20,40,60,80,100]
        result=self.analyze(values)
        self.assertFalse(any(p["type"]=="spike_extreme" for p in result["pivots"]))

    def test_major_reversal_reason_is_axis_validated(self):
        values=[10,30,50,80,100,85,65,45,20,30,50,75,95]
        result=self.analyze(values)
        reasons=[p["reason"] for p in result["pivots"] if p["type"]=="major_reversal"]
        self.assertTrue(reasons)
        for reason in reasons:
            self.assertIn("고정 Y축",reason)

    def test_every_pivot_has_reason(self):
        result=self.analyze([10,30,20,60,15,80,25,70,20])
        self.assertTrue(all(str(p.get("reason") or "").strip() for p in result["pivots"]))


if __name__=="__main__":
    unittest.main()
