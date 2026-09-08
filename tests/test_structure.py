"""Static deployment contracts: path moves must not leave broken runtime references."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DeploymentStructureTests(unittest.TestCase):
    def test_edge_relative_imports_exist(self):
        for source in (ROOT / 'supabase/functions').rglob('*.ts'):
            for relative in re.findall(r'(?:from\s*|import\s*)["\'](\.[^"\']+)["\']', source.read_text(encoding='utf8')):
                self.assertTrue((source.parent / relative).is_file(), f'{source}: {relative}')

    def test_workflow_python_entry_points_exist(self):
        for workflow in (ROOT / '.github/workflows').glob('*.yml'):
            text = workflow.read_text(encoding='utf8')
            for module in re.findall(r'python -m ((?:signals|operations|tracking|earnings_\w+)\.[\w.]+)', text):
                target = ROOT / 'backend' / (module.replace('.', '/') + '.py')
                self.assertTrue(target.is_file(), f'{workflow.name}: {module}')
            for target in re.findall(r'\bbackend/[\w/]+\.py\b', text):
                self.assertTrue((ROOT / target).is_file(), f'{workflow.name}: {target}')

    def test_legacy_asset_urls_are_generated_from_canonical_sources(self):
        for source in (ROOT / 'assets/js').rglob('*.js'):
            alias = ROOT / source.name
            text = alias.read_text(encoding='utf8')
            self.assertTrue(text.startswith('---\nlayout: null\n---\n'))
            self.assertIn('{% include_relative ' + source.relative_to(ROOT).as_posix() + ' %}', text)
            self.assertNotIn('function ', text)
        dashboard = (ROOT / 'dashboard-charts.js').read_text(encoding='utf8')
        self.assertLess(dashboard.index('assets/js/charts/analysis-chart-utils.js'), dashboard.index('assets/js/dashboard/dashboard-charts.js'))
