"""Dependency direction for Rehearsal and its Song association write use cases."""

import ast
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]


def _tree(path):
    return ast.parse((BACKEND / path).read_text(encoding='utf-8'))


def test_rehearsal_service_does_not_import_routes_or_http_modules():
    for node in ast.walk(_tree('rehearsal_service.py')):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or '']
        else:
            continue
        assert all(module.split('.')[0] not in {'routes', 'flask'} for module in modules)


def test_rehearsal_routes_delegate_transaction_ownership_to_service():
    tree = _tree('routes/content_writes.py')
    for route_name, service_name in (
            ('create_rehearsal', 'create_band_rehearsal'),
            ('update_rehearsal', 'update_band_rehearsal')):
        route = next(node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == route_name)
        calls = [node.func for node in ast.walk(route) if isinstance(node, ast.Call)]
        called_names = {node.id for node in calls if isinstance(node, ast.Name)}
        assert service_name in called_names
        assert '_run_write' not in called_names
        assert '_scope_for_write' not in called_names
        assert not any(isinstance(node, ast.Attribute) and node.attr in {'commit', 'rollback'}
                       for node in calls)


def test_rehearsal_service_reuses_shared_scope_values_and_edit_rule():
    tree = _tree('rehearsal_service.py')
    expected = {
        'content_write_scope': {'active_member_write_scope'},
        'content_write_rules': {'can_change_authored_content'},
        'content_write_values': {'text_value', 'visibility_value'},
        'content_visibility': {'media_visibility', 'rehearsal_visibility'},
    }
    for module, names in expected.items():
        imported = {alias.name for node in tree.body
                    if isinstance(node, ast.ImportFrom) and node.module == module
                    for alias in node.names}
        assert names <= imported
    assert '_can_change' not in {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}


def test_rehearsal_route_reuses_field_contract_and_no_longer_defines_private_helpers():
    tree = _tree('routes/content_writes.py')
    imports = {alias.name for node in tree.body
               if isinstance(node, ast.ImportFrom) and node.module == 'rehearsal_service'
               for alias in node.names}
    assert {'REHEARSAL_FIELDS', 'create_rehearsal', 'update_rehearsal'} <= imports
    definitions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert definitions.isdisjoint({'_date', '_songs_or_404', '_apply_rehearsal'})
