"""Dependency direction for comments, replies and shared interaction targets."""

import ast
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]


def _tree(path):
    return ast.parse((BACKEND / path).read_text(encoding='utf-8'))


def test_comment_service_and_shared_rules_do_not_import_routes_or_http_modules():
    for module in ('comment_service.py', 'comment_rules.py'):
        for node in ast.walk(_tree(module)):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or '']
            else:
                continue
            assert all(module.split('.')[0] not in {'routes', 'flask'} for module in modules)


def test_comment_routes_delegate_transaction_ownership_to_service():
    tree = _tree('routes/content_writes.py')
    for route_name, service_name in (
            ('_create_comment', 'create_comment'),
            ('update_comment_v2', 'update_comment'),
            ('delete_comment_v2', 'delete_comment')):
        route = next(node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == route_name)
        calls = [node.func for node in ast.walk(route) if isinstance(node, ast.Call)]
        names = {node.id for node in calls if isinstance(node, ast.Name)}
        assert service_name in names
        assert not {'_run_write', '_scope_for_write', '_can_delete', '_can_change'}.intersection(names)
        assert not any(isinstance(node, ast.Attribute) and node.attr in {'commit', 'rollback'}
                       for node in calls)
    definitions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert not {
        '_comment_parent_or_404', '_timestamp', '_author', '_load_comment_and_target',
        '_target_for_comment',
    }.intersection(definitions)
    assert '_comment_response' in definitions


def test_comment_and_vote_targets_share_one_scoped_lookup():
    for module in ('comment_service.py', 'routes/content_writes.py'):
        imports = {alias.name for node in _tree(module).body
                   if isinstance(node, ast.ImportFrom) and node.module == 'comment_rules'
                   for alias in node.names}
        assert '_target_for_comment' in imports
    definitions = {node.name for node in _tree('comment_rules.py').body
                   if isinstance(node, ast.FunctionDef)}
    assert '_target_for_comment' in definitions
    imports = {alias.name for node in _tree('comment_service.py').body
               if isinstance(node, ast.ImportFrom) and node.module == 'comment_rules'
               for alias in node.names}
    assert {'_owned_or_404', '_comment_target_kind', '_assert_comment_tree_safe'} <= imports


def test_comment_service_reuses_edit_rule_values_and_current_delete_capability():
    tree = _tree('comment_service.py')
    expected = {
        'content_write_rules': {'can_change_authored_content'},
        'content_write_values': {'text_value'},
        'content_write_scope': {'active_member_write_scope'},
        'band_access': {'content_capabilities'},
    }
    for module, names in expected.items():
        imported = {alias.name for node in tree.body
                    if isinstance(node, ast.ImportFrom) and node.module == module
                    for alias in node.names}
        assert names <= imported
    for name, lock_actor in (('create_comment', False), ('update_comment', False), ('delete_comment', True)):
        operation = next(node for node in tree.body
                         if isinstance(node, ast.FunctionDef) and node.name == name)
        scope_calls = [node for node in ast.walk(operation)
                       if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                       and node.func.id == '_scope_for_write']
        assert len(scope_calls) == 1
        keywords = {keyword.arg: ast.literal_eval(keyword.value) for keyword in scope_calls[0].keywords}
        expected_keywords = {'lock': True, 'lock_actor': True} if lock_actor else {'lock': True}
        assert keywords == expected_keywords
