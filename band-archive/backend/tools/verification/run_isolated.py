"""Run baseline tests with owned output and no inherited provider settings."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

BASELINE = 'd5cae8795f9916ea68ba963ad07d10c3af58cb07'
BLOCKED = ('PG', 'POSTGRES', 'DATABASE', 'DB', 'REHEARSAL_', 'AUTH_', 'JWT_',
           'TOKEN_', 'STORAGE_', 'R2_', 'AWS_', 'S3_', 'MINIO_', 'SUPABASE_',
           'CLOUDFLARE_', 'NAVER_', 'SQLALCHEMY_', 'PYTEST_', 'DOTENV_',
           'FLASK_', 'MAIL', 'SMTP', 'GMAIL', 'ENABLED_')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', required=True, type=Path)
    parser.add_argument('--output-parent', required=True, type=Path)
    parser.add_argument('--pg-bin', required=True, type=Path)
    parser.add_argument('pytest_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    backend = args.backend.resolve()
    evidence = json.loads((backend.parent / 'verification-source.json').read_text(encoding='utf-8'))
    if evidence['commit'] != BASELINE:
        raise SystemExit('Unsupported source baseline')
    # Detect local source/test edits after preparation. No secrets are read.
    for relative, expected in evidence['files'].items():
        if hashlib.sha256((backend / relative).read_bytes()).hexdigest() != expected:
            raise SystemExit('Prepared source changed: ' + relative)
    pg_bin = args.pg_bin.resolve()
    suffix = '.exe' if os.name == 'nt' else ''
    for binary in ('postgres', 'initdb'):
        if not (pg_bin / (binary + suffix)).is_file():
            raise SystemExit('Owned PG17 binaries required')
    version = subprocess.run([str(pg_bin / ('postgres' + suffix)), '--version'],
                             check=True, capture_output=True, text=True)
    if not re.search(r'\b17\.', version.stdout):
        raise SystemExit('PostgreSQL 17 is required')
    if os.name != 'nt' and os.geteuid() == 0:
        raise SystemExit('Run verification as a non-root user (initdb requirement)')
    output = args.output_parent.resolve() / ('verification-' + uuid.uuid4().hex)
    output.mkdir(parents=False, exist_ok=False)
    temporary = output / 'temp'
    temporary.mkdir()
    environment = os.environ.copy()
    for name in tuple(environment):
        upper = name.upper()
        if (upper.startswith(BLOCKED)
                or upper in {'PYTHONPATH', 'PYTHONHOME', 'SECRET_KEY', 'CORS_ALLOWED_ORIGINS',
                             'ANYTHING_VERIFICATION_BOOTSTRAP_READY', 'ANYTHING_VERIFICATION_PG_BIN'}
                or any(word in upper for word in ('PASSWORD', 'PASSWD', 'PRIVATE_KEY',
                                                 'CLIENT_SECRET', 'SERVICE_ROLE_KEY', 'PUBLISHABLE_KEY'))):
            environment.pop(name, None)
    bootstrap = Path(__file__).resolve().parent / 'bootstrap'
    environment.update({
        'RUN_PRIVATE_PG_INTEGRATION': '1', 'ANYTHING_ISOLATED_TEST': '1',
        'PYTHON_DOTENV_DISABLED': '1', 'PYTHONPATH': str(bootstrap),
        'ANYTHING_VERIFICATION_PG_BIN': str(pg_bin),
        'TEMP': str(temporary), 'TMP': str(temporary), 'TMPDIR': str(temporary),
    })
    extra = args.pytest_args
    if extra[:1] == ['--']:
        extra = extra[1:]
    # Keep the interface intentionally small: no plugin/config/output override.
    # Selection must be an explicit baseline tests path, not an external module.
    for value in extra:
        if value == '--collect-only':
            continue
        target = value.split('::', 1)[0]
        if (value.startswith('-') or not target.startswith('tests/')
                or '..' in Path(target).parts or not (backend / target).exists()):
            raise SystemExit('Only baseline tests/ paths and --collect-only are supported')
    command = [sys.executable, '-m', 'pytest', '-q', '-ra', '-p', 'no:cacheprovider',
               '--basetemp', str(output / 'pytest'),
               '--junitxml', str(output / 'junit.xml'), *extra]
    # Fail closed if sitecustomize could not import python-dotenv.
    probe = subprocess.run([sys.executable, '-c',
                            "import os; assert os.environ.get('ANYTHING_VERIFICATION_BOOTSTRAP_READY') == '1'"],
                           cwd=backend, env=environment, capture_output=True)
    if probe.returncode:
        raise SystemExit('Dotenv isolation bootstrap did not initialize')
    print('PRIVATE_OUTPUT_DIR=' + str(output), flush=True)
    started = time.monotonic()
    with (output / 'stdout.log').open('w', encoding='utf-8') as stdout, \
            (output / 'stderr.log').open('w', encoding='utf-8') as stderr:
        try:
            result = subprocess.run(command, cwd=backend, env=environment,
                                    stdout=stdout, stderr=stderr, timeout=1800)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
            print('TEST_TIMEOUT=1800 seconds; inspect owned child processes before retry', flush=True)
    metadata = {'commit': BASELINE, 'pytest_args': extra, 'exit_code': code,
                'duration_seconds': round(time.monotonic() - started, 3),
                'postgres_version': version.stdout.strip(),
                'timeout_seconds': 1800, 'hashes': {}}
    for name in ('stdout.log', 'stderr.log', 'junit.xml'):
        path = output / name
        if path.exists():
            metadata['hashes'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (output / 'run.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    lines = (output / 'stdout.log').read_text(encoding='utf-8', errors='replace').splitlines()
    summaries = [line for line in lines if re.match(r'^\s*\d+\s+(passed|failed|error|errors|skipped)\b', line)]
    print('TEST_SUMMARY=' + (' | '.join(summaries[-3:]) or 'No count summary; inspect private logs'))
    print('PYTEST_EXIT=' + str(code))
    print('DURATION_SECONDS=' + str(metadata['duration_seconds']))
    for name, digest in metadata['hashes'].items():
        print(name.upper() + '_SHA256=' + digest)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
