"""Dependency direction for atomic Song and Rehearsal parent deletion."""

import ast
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]


def _tree(path):
    return ast.parse((BACKEND / path).read_text(encoding='utf-8'))


def test_parent_services_do_not_import_routes_or_http_response_modules():
    for module in ('song_service.py', 'rehearsal_service.py'):
        for node in ast.walk(_tree(module)):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or '']
            else:
                continue
            assert all(module.split('.')[0] not in {'routes', 'flask'} for module in modules)


def test_parent_delete_routes_delegate_transaction_ownership_to_services():
    tree = _tree('routes/content_writes.py')
    for domain in ('song', 'rehearsal'):
        route = next(node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == f'delete_{domain}')
        calls = [node.func for node in ast.walk(route) if isinstance(node, ast.Call)]
        names = {node.id for node in calls if isinstance(node, ast.Name)}
        assert f'delete_band_{domain}' in names
        assert not {'_run_write', '_scope_for_write', '_can_delete'}.intersection(names)
        assert not any(isinstance(node, ast.Attribute) and node.attr in {'commit', 'rollback'}
                       for node in calls)
    definitions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert not {'_assert_song_delete_safe', '_assert_rehearsal_delete_safe'}.intersection(definitions)


def test_parent_deletes_keep_existing_capabilities_cleanup_and_transaction_boundaries():
    for domain in ('song', 'rehearsal'):
        tree = _tree(f'{domain}_service.py')
        delete = next(node for node in tree.body
                      if isinstance(node, ast.FunctionDef) and node.name == f'delete_{domain}')
        calls = [node for node in ast.walk(delete) if isinstance(node, ast.Call)]
        names = {node.func.id for node in calls if isinstance(node.func, ast.Name)}
        assert {'content_capabilities', 'cleanup_terminal_parent_upload_sessions',
                f'_assert_{domain}_delete_safe', f'_run_{domain}_write',
                'prepare_response'}.issubset(names)
        cleanup_imports = {alias.name for node in ast.walk(delete)
                           if isinstance(node, ast.ImportFrom) and node.module == 'deletion_service'
                           for alias in node.names}
        assert 'cleanup_terminal_parent_upload_sessions' in cleanup_imports
        scope_calls = [node for node in calls if isinstance(node.func, ast.Name)
                       and node.func.id == '_scope_for_write']
        assert len(scope_calls) == 1
        assert {keyword.arg: ast.literal_eval(keyword.value) for keyword in scope_calls[0].keywords} == {
            'lock': True, 'lock_actor': True,
        }
