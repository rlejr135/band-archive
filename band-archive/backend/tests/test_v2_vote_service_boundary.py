"""Authenticated vote and startup recovery ownership, without route back-edges."""

import ast
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]


def _tree(path):
    return ast.parse((BACKEND / path).read_text(encoding='utf-8'))


def test_vote_and_recovery_services_do_not_import_routes_or_http_modules():
    for module in ('v2_vote_service.py', 'upload_recovery_service.py'):
        for node in ast.walk(_tree(module)):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or '']
            else:
                continue
            assert all(module.split('.')[0] not in {'routes', 'flask'} for module in modules)


def test_vote_routes_delegate_without_own_database_or_transaction_wrapper():
    tree = _tree('routes/content_writes.py')
    for route_name, service_name in (
            ('_apply_media_vote', 'set_media_vote'),
            ('_apply_suggestion_vote', 'set_song_suggestion_vote')):
        route = next(node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == route_name)
        calls = [node.func for node in ast.walk(route) if isinstance(node, ast.Call)]
        names = {node.id for node in calls if isinstance(node, ast.Name)}
        assert service_name in names
        assert {'_payload', '_vote_value', 'jsonify'} <= names
        assert not {'_run_write', '_scope_for_write'}.intersection(names)
        assert not any(isinstance(node, ast.Attribute) and node.attr in {'commit', 'rollback', 'query'}
                       for node in calls)
    definitions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert not {
        '_scope_for_write', '_begin_write', '_run_write', '_v2_voter_hash', '_suggestion_or_404',
    }.intersection(definitions)
    imports = {node.module for node in tree.body if isinstance(node, ast.ImportFrom)}
    assert 'extensions' not in imports


def test_vote_service_owns_both_use_cases_and_preserves_shared_scope_target_dependencies():
    tree = _tree('v2_vote_service.py')
    expected = {
        'comment_rules': {'_owned_or_404', '_target_for_comment'},
        'content_write_scope': {'active_member_write_scope'},
        'auth_service': {'current_app_user'},
        'sqlalchemy.exc': {'IntegrityError'},
    }
    for module, names in expected.items():
        imported = {alias.name for node in tree.body
                    if isinstance(node, ast.ImportFrom) and node.module == module
                    for alias in node.names}
        assert names <= imported
    for name in ('set_media_vote', 'set_song_suggestion_vote'):
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == name)
        calls = {node.func.id for node in ast.walk(function)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        assert {'_scope_for_write', '_run_vote_write', 'prepare_response'} <= calls


def test_startup_imports_recovery_directly_and_upload_route_has_no_recovery_shim():
    imports = [node for node in ast.walk(_tree('app.py')) if isinstance(node, ast.ImportFrom)
               and any(alias.name == 'recover_multipart_upload_sessions' for alias in node.names)]
    assert imports
    assert all(node.module == 'upload_recovery_service' for node in imports)
    upload_tree = _tree('routes/uploads.py')
    assert 'recover_multipart_upload_sessions' not in {
        node.name for node in upload_tree.body if isinstance(node, ast.FunctionDef)}
    assert not any(alias.name == 'recover_multipart_upload_sessions'
                   for node in upload_tree.body if isinstance(node, ast.ImportFrom)
                   for alias in node.names)


def test_recovery_coordinator_keeps_algorithms_separate_from_transaction_ownership():
    tree = _tree('upload_recovery_service.py')
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == 'recover_multipart_upload_sessions')
    calls = [node.func for node in ast.walk(function) if isinstance(node, ast.Call)]
    names = {node.id for node in calls if isinstance(node, ast.Name)}
    assert {'inspect', 'recover_sessions', 'recover_direct_sessions'} <= names
    assert not any(isinstance(node, ast.Attribute) and node.attr in {'commit', 'rollback'}
                   for node in calls)
