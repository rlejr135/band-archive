"""Dependency direction for logo and featured-photo selection use cases."""

import ast
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]


def _tree(path):
    return ast.parse((BACKEND / path).read_text(encoding='utf-8'))


def test_gallery_selection_services_do_not_import_routes_or_http_response_modules():
    for module in ('band_logo_service.py', 'gallery_featured_service.py'):
        for node in ast.walk(_tree(module)):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or '']
            else:
                continue
            assert all(module.split('.')[0] not in {'routes', 'flask'} for module in modules)


def test_gallery_selection_routes_delegate_transaction_ownership_to_services():
    tree = _tree('routes/content_writes.py')
    for route_name, service_name in (
            ('set_band_logo', 'update_band_logo_selection'),
            ('set_gallery_featured_v2', 'set_gallery_featured')):
        route = next(node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == route_name)
        calls = [node.func for node in ast.walk(route) if isinstance(node, ast.Call)]
        called_names = {node.id for node in calls if isinstance(node, ast.Name)}
        assert service_name in called_names
        assert '_run_write' not in called_names
        assert '_scope_for_write' not in called_names
        assert not any(isinstance(node, ast.Attribute) and node.attr in {'commit', 'rollback'}
                       for node in calls)


def test_gallery_selection_services_reuse_existing_scope_and_visibility_rules():
    expected = {
        'band_logo_service.py': {
            'band_content_predicates': {'_creator_is_scoped_sql', '_not_pending_delete'},
            'deletion_service': {'actor_is_eligible', 'load_current_actor', 'lock_band_and_membership'},
        },
        'gallery_featured_service.py': {
            'content_write_scope': {'active_member_write_scope'},
            'content_write_values': {'VISIBILITIES'},
            'comment_rules': {'_owned_or_404'},
        },
    }
    for module, dependencies in expected.items():
        tree = _tree(module)
        for dependency, names in dependencies.items():
            imported = {alias.name for node in tree.body
                        if isinstance(node, ast.ImportFrom) and node.module == dependency
                        for alias in node.names}
            assert names <= imported
