"""Dependency boundaries for v2 playback URL coordination."""

import ast
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]


def _tree(path):
    return ast.parse((BACKEND / path).read_text(encoding='utf-8'))


def test_access_service_does_not_import_serializers_or_routes():
    tree = _tree('media_access_service.py')
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or '']
        else:
            continue
        assert all(not (name == 'routes' or name.startswith('routes.')
                        or name in {'v2_content_serializers', 'v2_remaining_read_serializers'})
                   for name in names)


def test_serializers_do_not_sign_storage_urls_directly():
    for path in ('v2_content_serializers.py', 'v2_remaining_read_serializers.py'):
        for node in ast.walk(_tree(path)):
            assert not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == 'storage' and node.func.attr == 'generate_url'), path


def test_access_service_imports_source_selector_directly():
    imports = [node for node in ast.walk(_tree('media_access_service.py'))
               if isinstance(node, ast.ImportFrom)
               and any(alias.name == 'select_media_source_keys' for alias in node.names)]
    assert imports and all(node.module == 'media_sources' for node in imports)
