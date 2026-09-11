from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sources.korea_export_intramonth import (  # noqa: E402
    ReleaseLink,
    _reported_daily_average,
    _workdays,
    independent_segment_rows,
    parse_snapshot,
)


class KoreaExportIntramonthTests(unittest.TestCase):
    def test_current_kcs_text_variants_parse_workdays_and_daily_average(self):
        text = "※조업일수[(’25)8.5 일,(’26)8.5 일] 고려 시 일평균 수출액[(’25)35.6,(’26)41.1]"
        self.assertEqual(_workdays(text), 8.5)
        self.assertEqual(_reported_daily_average(text), 41.1)

    def test_snapshot_uses_official_amount_and_workdays(self):
        markup = """
        <div>※조업일수[(’25)8.5 일,(’26)8.5 일] 고려 시 일평균 수출액[(’25)35.6,(’26)41.1]</div>
        <table><tr><th>수출(전년동기대비)</th><td>30,260</td><td>34,973</td><td>15.6%</td></tr></table>
        """
        link = ReleaseLink(
            title="2026년 9월 1일 ~ 9월 10일 수출입 현황 [잠정치]",
            ntt_sn="1",
            ntt_url="",
            stage="d10",
            reference_month=date(2026, 9, 1),
            period_end=date(2026, 9, 10),
            published_on=date(2026, 9, 11),
        )
        row = parse_snapshot(markup, link)
        self.assertEqual(row.cumulative_workdays, 8.5)
        self.assertEqual(row.cumulative_export_musd, 34973.0)

    def test_independent_segments_use_incremental_amount_and_workdays(self):
        base = dict(published_on=None, source_url="official")
        from sources.korea_export_intramonth import ExportSnapshot

        snapshots = [
            ExportSnapshot("d10", date(2026, 8, 1), date(2026, 8, 10), 20000, 7.0, **base),
            ExportSnapshot("d20", date(2026, 8, 1), date(2026, 8, 20), 40000, 14.0, **base),
            ExportSnapshot("month_end", date(2026, 8, 1), date(2026, 8, 31), 60000, 21.0, **base),
        ]
        rows = independent_segment_rows(snapshots)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["value"] == rows[0]["value"] for row in rows))
        self.assertEqual([row["observation_date"] for row in rows], ["2026-08-10", "2026-08-20", "2026-08-31"])


if __name__ == "__main__":
    unittest.main()
