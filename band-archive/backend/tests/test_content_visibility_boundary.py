"""Import-boundary checks for the extracted content visibility rules."""

import ast
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
VISIBILITY_NAMES = {
    'visible_to_scope', 'belongs_to_scope', 'graph_is_scoped',
    'song_visibility', 'rehearsal_visibility', 'media_visibility',
    '_band_visibility', 'personal_log_visibility', 'gallery_visibility',
    'announcement_visibility', 'suggestion_visibility',
}
PRODUCT_CONSUMERS = (
    'routes/content_reads.py',
    'routes/content_writes.py',
    'routes/songs.py',
    'routes/rehearsals.py',
    'routes/comments.py',
    'routes/personal_logs.py',
    'routes/gallery.py',
    'media_processing.py',
    'models.py',
    'v2_content_serializers.py',
    'v2_remaining_read_serializers.py',
)


def _tree(path):
    return ast.parse((BACKEND / path).read_text(encoding='utf-8'))


def test_content_visibility_has_no_reverse_product_imports():
    tree = _tree('content_visibility.py')
    product_modules = {
        'routes', 'routes.content_reads', 'routes.content_writes', 'routes.songs',
        'routes.rehearsals', 'routes.comments', 'routes.personal_logs', 'routes.gallery',
        'v2_content_serializers', 'v2_remaining_read_serializers',
    }
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported.isdisjoint(product_modules)


def test_visibility_helpers_are_defined_only_in_the_new_module():
    for path in ('v2_content_serializers.py', 'v2_remaining_read_serializers.py'):
        definitions = {
            node.name for node in _tree(path).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert definitions.isdisjoint(VISIBILITY_NAMES)
    definitions = {
        node.name for node in _tree('content_visibility.py').body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert definitions == VISIBILITY_NAMES


def test_product_consumers_do_not_import_visibility_from_serializers():
    serializer_modules = {'v2_content_serializers', 'v2_remaining_read_serializers'}
    for path in PRODUCT_CONSUMERS:
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.ImportFrom) and node.module in serializer_modules:
                imported_names = {alias.name for alias in node.names}
                assert imported_names.isdisjoint(VISIBILITY_NAMES), path
