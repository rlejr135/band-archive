"""Dependency direction and shared editing rules for Song write use cases."""

import ast
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]


def _tree(path):
    return ast.parse((BACKEND / path).read_text(encoding='utf-8'))


def test_song_service_and_shared_edit_rule_do_not_import_routes_or_http_modules():
    for module in ('song_service.py', 'content_write_rules.py'):
        for node in ast.walk(_tree(module)):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or '']
            else:
                continue
            assert all(module.split('.')[0] not in {'routes', 'flask'} for module in modules)


def test_song_routes_delegate_transaction_ownership_to_service():
    tree = _tree('routes/content_writes.py')
    for route_name, service_name in (
            ('create_song', 'create_band_song'), ('update_song', 'update_band_song')):
        route = next(node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == route_name)
        calls = [node.func for node in ast.walk(route) if isinstance(node, ast.Call)]
        called_names = {node.id for node in calls if isinstance(node, ast.Name)}
        assert service_name in called_names
        assert '_run_write' not in called_names
        assert '_scope_for_write' not in called_names
        assert not any(isinstance(node, ast.Attribute) and node.attr in {'commit', 'rollback'}
                       for node in calls)


def test_song_and_other_content_writes_share_one_editing_rule():
    for module in ('song_service.py', 'routes/content_writes.py'):
        tree = _tree(module)
        names = {alias.name for node in tree.body
                 if isinstance(node, ast.ImportFrom) and node.module == 'content_write_rules'
                 for alias in node.names}
        assert 'can_change_authored_content' in names
        assert '_can_change' not in {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    definitions = {node.name for node in _tree('content_write_rules.py').body
                   if isinstance(node, ast.FunctionDef)}
    assert definitions == {'can_change_authored_content'}


def test_song_service_reuses_shared_values_and_route_field_contract():
    tree = _tree('song_service.py')
    names = {alias.name for node in tree.body
             if isinstance(node, ast.ImportFrom) and node.module == 'content_write_values'
             for alias in node.names}
    assert {'text_value', 'visibility_value'} <= names
    route_imports = {alias.name for node in _tree('routes/content_writes.py').body
                     if isinstance(node, ast.ImportFrom) and node.module == 'song_service'
                     for alias in node.names}
    assert {'SONG_FIELDS', 'create_song', 'update_song'} <= route_imports
    assert '_apply_song' not in {node.name for node in _tree('routes/content_writes.py').body
                                 if isinstance(node, ast.FunctionDef)}
