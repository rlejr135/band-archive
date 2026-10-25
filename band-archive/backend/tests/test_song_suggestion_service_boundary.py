"""Dependency boundary for the extracted suggestion promotion use case."""

import ast
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]


def _tree(relative_path):
    return ast.parse((BACKEND / relative_path).read_text(encoding='utf-8'))


def test_proceed_service_and_shared_scope_do_not_import_routes():
    for module in ('song_suggestion_service.py', 'content_write_scope.py'):
        for node in ast.walk(_tree(module)):
            if isinstance(node, ast.Import):
                assert all(item.name != 'routes' and not item.name.startswith('routes.') for item in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module == 'routes' or (node.module or '').startswith('routes.'))


def test_proceed_route_delegates_without_own_transaction_wrapper():
    route = next(node for node in _tree('routes/content_writes.py').body
                 if isinstance(node, ast.FunctionDef) and node.name == 'proceed_suggestion_v2')
    calls = [node.func for node in ast.walk(route) if isinstance(node, ast.Call)]
    called_names = {node.id for node in calls if isinstance(node, ast.Name)}
    assert 'proceed_song_suggestion' in called_names
    assert '_run_write' not in called_names
