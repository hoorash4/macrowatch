import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sources.equifax_level_parser import extract_equifax_levels


class EquifaxLevelParserTests(unittest.TestCase):
    def test_modern_sbdfi_first_table_layout(self):
        text = """
        Equifax Small Business Delinquency (SBDI) & Default Indices (SBDFI)
        SBDFI SBDI 31-90 Days SBDI 91-180 Days
        3.27% (Level) ▼4bps (M/M) ▼5bps (Y/Y)
        1.72% (Level) ▲3bps (M/M) ▼1bps (Y/Y)
        0.73% (Level) ▲1bps (M/M) ▲3bps (Y/Y)
        """
        self.assertEqual(extract_equifax_levels(text), (1.72, 0.73, 3.27))

    def test_modern_different_header_order_maps_values_by_header(self):
        text = """
        SBDI 31-90 Days SBDFI SBDI 91-180 Days
        1.71% (Level) ▲1bps (M/M) ▲2bps (Y/Y)
        3.30% (Level) ▼1bps (M/M) ▲4bps (Y/Y)
        0.74% (Level) ▲1bps (M/M) ▲2bps (Y/Y)
        """
        self.assertEqual(extract_equifax_levels(text), (1.71, 0.74, 3.30))

    def test_local_metric_blocks_still_work(self):
        text = """
        SBDI 31-90 Days ▲0bps (M/M) 1.71% (Level)
        SBDI 91-180 Days ▲2bps (M/M) 0.57% (Level)
        SBDFI ▲5bps (M/M) 2.91% (Level)
        """
        self.assertEqual(extract_equifax_levels(text), (1.71, 0.57, 2.91))

    def test_narrative_layout_still_works(self):
        text = """
        The SBDI 31-90 Days Past Due edged down to 1.19%.
        The SBDI 91-180 Days Past Due fell to 0.34%.
        Defaults eased to 1.77%.
        """
        self.assertEqual(extract_equifax_levels(text), (1.19, 0.34, 1.77))


if __name__ == "__main__":
    unittest.main()
