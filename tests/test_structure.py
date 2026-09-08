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

    def test_root_contains_only_required_document_entries(self):
        self.assertEqual({p.name for p in ROOT.glob('*.md')}, {'AGENTS.md', 'README.md'})
        self.assertEqual(list(ROOT.glob('*.js')), [])
        self.assertEqual(list(ROOT.glob('*.css')), [])
        for name in ['CODE_STRUCTURE.md', 'HANDOFF.md', 'LIQUIDITY_SPEC.md', 'SECURITY.md']:
            self.assertTrue((ROOT / 'docs' / name).is_file())
