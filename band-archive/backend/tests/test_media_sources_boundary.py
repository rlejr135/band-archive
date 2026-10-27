"""Dependency checks for source rules, not a replacement for playback contracts."""

import ast
from pathlib import Path
import subprocess
import sys


BACKEND = Path(__file__).resolve().parents[1]
CONSUMERS = (
    'media_processing.py',
    'media_access_service.py',
    'routes/content_writes.py',
    'routes/songs.py',
    'routes/personal_logs.py',
)


def test_media_sources_import_is_standalone_without_application_dependencies():
    # A fresh isolated interpreter detects transitive imports too. Loading the
    # file by path avoids importing application bootstrap or the test package.
    script = '''
import importlib.util
import sys
spec = importlib.util.spec_from_file_location('source_rules_under_test', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
for name in sys.modules:
    assert name.split('.')[0] not in {
        'flask', 'sqlalchemy', 'models', 'storage', 'extensions',
        'media_processing', 'deletion_worker', 'routes',
    }, name
assert callable(module.select_media_source_keys)
'''
    subprocess.run(
        [sys.executable, '-I', '-c', script, str(BACKEND / 'media_sources.py')],
        check=True, capture_output=True, text=True, timeout=10,
    )


def test_all_product_source_consumers_import_the_rule_module_directly():
    for path in CONSUMERS:
        tree = ast.parse((BACKEND / path).read_text(encoding='utf-8'))
        imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        source_imports = [node for node in imports if any(
            alias.name == 'select_media_source_keys' for alias in node.names
        )]
        assert source_imports, path
        assert all(node.module == 'media_sources' for node in source_imports), path


def test_source_rules_have_no_application_imports_or_duplicate_implementations():
    tree = ast.parse((BACKEND / 'media_sources.py').read_text(encoding='utf-8'))
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree))
    expected = {'_canonical_or_legacy_key', 'select_media_source_keys'}
    assert {node.name for node in tree.body if isinstance(node, ast.FunctionDef)} == expected
    for path in CONSUMERS:
        consumer = ast.parse((BACKEND / path).read_text(encoding='utf-8'))
        assert not expected.intersection(
            node.name for node in ast.walk(consumer) if isinstance(node, ast.FunctionDef)
        ), path
