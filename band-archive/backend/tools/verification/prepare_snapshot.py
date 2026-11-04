"""Export one fixed Git baseline and adapt only PG fixture binary selection."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

BASELINE = 'd5cae8795f9916ea68ba963ad07d10c3af58cb07'
FIXTURE = 'tests/test_data_api_security_migration.py'
OLD_BIN = "PG_BIN = Path(__file__).parents[1] / 'tools' / '.state' / 'postgresql117' / 'pgsql' / 'bin'"
NEW_BIN = """# Verification-only platform adapter; assertions and lifecycle remain unchanged.
import os
PG_BIN = Path(os.environ['ANYTHING_VERIFICATION_PG_BIN'])
PG_EXE_SUFFIX = '.exe' if os.name == 'nt' else ''"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    repository = args.repository.resolve()
    destination = args.destination.resolve()
    if destination.exists():
        raise SystemExit('Destination must not exist; no existing files are changed')
    resolved = subprocess.run(['git', 'rev-parse', '--verify', BASELINE + '^{commit}'],
                              cwd=repository, check=True, capture_output=True, text=True).stdout.strip()
    if resolved != BASELINE:
        raise SystemExit('Baseline resolution mismatch')
    archive = subprocess.run(['git', 'archive', '--format=tar', BASELINE, 'backend'],
                             cwd=repository, check=True, capture_output=True).stdout
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as source:
        for member in source:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or path.parts[0] != 'backend':
                raise SystemExit('Unexpected archive path')
            if member.isdir():
                continue
            if not member.isfile():
                raise SystemExit('Links and special entries are not supported')
            relative = PurePosixPath(*path.parts[1:])
            if any(part.startswith(('.env', '.venv')) or part in {'.state', 'secrets.yaml'}
                   for part in relative.parts):
                continue
            if relative.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.pem', '.key'}:
                continue
            stream = source.extractfile(member)
            if stream is None:
                raise SystemExit('Cannot read regular archive entry')
            files[relative.as_posix()] = stream.read()
    original = files[FIXTURE]
    text = original.decode('utf-8')
    if text.count(OLD_BIN) != 1 or text.count("'initdb.exe'") != 2 or text.count("'postgres.exe'") != 1:
        raise SystemExit('Fixture differs from reviewed baseline; refuse broad replacement')
    adapted = text.replace(OLD_BIN, NEW_BIN).replace("'initdb.exe'", "('initdb' + PG_EXE_SUFFIX)")
    adapted = adapted.replace("'postgres.exe'", "('postgres' + PG_EXE_SUFFIX)")
    files[FIXTURE] = adapted.encode('utf-8')
    destination.mkdir(parents=False, exist_ok=False)
    backend = destination / 'backend'
    for name, content in files.items():
        target = backend / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(content)
    import difflib
    patch = ''.join(difflib.unified_diff(text.splitlines(keepends=True),
                                       adapted.splitlines(keepends=True),
                                       fromfile='baseline/' + FIXTURE, tofile='verification/' + FIXTURE))
    (destination / 'fixture-os-adapter.diff').write_text(patch, encoding='utf-8')
    evidence = {'commit': BASELINE, 'fixture_adapter': FIXTURE,
                'fixture_original_sha256': hashlib.sha256(original).hexdigest(),
                'fixture_adapted_sha256': hashlib.sha256(files[FIXTURE]).hexdigest(),
                'files': {name: hashlib.sha256(content).hexdigest()
                          for name, content in sorted(files.items())}}
    (destination / 'verification-source.json').write_text(
        json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    print('Prepared fixed baseline; only fixture executable/path selection adapted.')
    print('Review fixture-os-adapter.diff before running any tests.')


if __name__ == '__main__':
    main()
