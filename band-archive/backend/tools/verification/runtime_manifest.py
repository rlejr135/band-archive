"""Record installed distributions without importing the application or secrets."""
import argparse
import importlib.metadata
import json
import platform
from pathlib import Path
import sys


def capture():
    packages = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get('Name', '')
        normalized = name.lower().replace('_', '-').replace('.', '-')
        if not normalized or normalized in packages:
            raise SystemExit('Missing or duplicate distribution name; inspect environment')
        packages[normalized] = {
            'version': distribution.version,
            'location': str(Path(distribution.locate_file('')).resolve()),
        }
    return {'python': sys.version, 'executable': sys.executable,
            'platform': platform.platform(), 'machine': platform.machine(),
            'packages': dict(sorted(packages.items()))}


def compare(before, after, allowed):
    for field in ('python', 'executable', 'platform', 'machine'):
        if before[field] != after[field]:
            raise SystemExit('Runtime identity changed: ' + field)
    removed = set(before['packages']) - set(after['packages'])
    changed = {name for name in before['packages'] if name in after['packages']
               and before['packages'][name] != after['packages'][name]}
    added = set(after['packages']) - set(before['packages'])
    if removed or changed or added - allowed:
        raise SystemExit('Manifest mismatch: removed=' + str(sorted(removed))
                         + ' changed=' + str(sorted(changed))
                         + ' unapproved_additions=' + str(sorted(added - allowed)))
    print('Runtime distributions unchanged; approved additions=' + str(sorted(added)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('capture', 'compare'))
    parser.add_argument('first', type=Path)
    parser.add_argument('second', type=Path, nargs='?')
    parser.add_argument('--allow-added', action='append', default=[])
    args = parser.parse_args()
    if args.operation == 'capture':
        with args.first.open('x', encoding='utf-8') as stream:
            json.dump(capture(), stream, indent=2)
            stream.write('\n')
    else:
        if args.second is None:
            parser.error('compare requires two manifests')
        compare(json.loads(args.first.read_text(encoding='utf-8')),
                json.loads(args.second.read_text(encoding='utf-8')),
                set(args.allow_added))
