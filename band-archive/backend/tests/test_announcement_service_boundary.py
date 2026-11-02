"""Dependency direction of the singleton announcement write use cases."""

import ast
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]


def _tree(path):
    return ast.parse((BACKEND / path).read_text(encoding='utf-8'))


def test_announcement_service_does_not_import_routes_or_http_response_modules():
    for node in ast.walk(_tree('announcement_service.py')):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or '']
        else:
            continue
        assert all(module.split('.')[0] not in {'routes', 'flask'} for module in modules)


def test_announcement_routes_delegate_transaction_ownership_to_service():
    tree = _tree('routes/content_writes.py')
    for route_name, service_name in (
            ('put_announcement_v2', 'put_announcement'),
            ('clear_announcement_v2', 'clear_announcement')):
        route = next(node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == route_name)
        calls = [node.func for node in ast.walk(route) if isinstance(node, ast.Call)]
        called_names = {node.id for node in calls if isinstance(node, ast.Name)}
        assert service_name in called_names
        assert '_run_write' not in called_names
        assert '_scope_for_write' not in called_names
        assert not any(isinstance(node, ast.Attribute) and node.attr in {'commit', 'rollback'}
                       for node in calls)


def test_announcement_service_reuses_shared_value_validation_and_field_contract():
    tree = _tree('announcement_service.py')
    imports = {alias.name for node in tree.body
               if isinstance(node, ast.ImportFrom) and node.module == 'content_write_values'
               for alias in node.names}
    assert {'text_value', 'visibility_value'} <= imports
    assert not {'_text', '_visibility'}.intersection(
        node.name for node in tree.body if isinstance(node, ast.FunctionDef))
    route_imports = {alias.name for node in _tree('routes/content_writes.py').body
                     if isinstance(node, ast.ImportFrom) and node.module == 'announcement_service'
                     for alias in node.names}
    assert {'ANNOUNCEMENT_FIELDS', 'put_announcement', 'clear_announcement'} <= route_imports
