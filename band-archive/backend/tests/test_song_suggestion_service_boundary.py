"""Dependency boundary for the extracted suggestion promotion use case."""

import ast
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]


def _tree(relative_path):
    return ast.parse((BACKEND / relative_path).read_text(encoding='utf-8'))


def test_proceed_service_and_shared_scope_do_not_import_routes():
    for module in ('song_suggestion_service.py', 'content_write_scope.py', 'content_write_values.py'):
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


def test_crud_routes_delegate_without_own_transaction_wrapper():
    tree = _tree('routes/content_writes.py')
    for route_name, service_name in (
            ('create_suggestion_v2', 'create_song_suggestion'),
            ('update_suggestion_v2', 'update_song_suggestion'),
            ('delete_suggestion_v2', 'delete_song_suggestion')):
        route = next(node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == route_name)
        called_names = {node.func.id for node in ast.walk(route)
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        assert service_name in called_names
        assert '_run_write' not in called_names


def test_content_writes_and_suggestion_service_share_value_validation():
    expected = {'text_value', 'visibility_value', 'external_link_value'}
    for module in ('routes/content_writes.py', 'song_suggestion_service.py'):
        tree = _tree(module)
        imports = {alias.name for node in tree.body
                   if isinstance(node, ast.ImportFrom) and node.module == 'content_write_values'
                   for alias in node.names}
        assert expected <= imports
        assert not {'_text', '_visibility', '_external_link'}.intersection(
            node.name for node in tree.body if isinstance(node, ast.FunctionDef))
