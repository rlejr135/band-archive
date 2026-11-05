import json
from pathlib import Path
import subprocess
import sys

def inventory():
    result = subprocess.run(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\n'],
                            check=True, capture_output=True, text=True)
    return dict(line.split('\t', 1) for line in result.stdout.splitlines())

mode = sys.argv[1]
if mode == 'capture':
    Path(sys.argv[2]).write_text(json.dumps(inventory(), indent=2, sort_keys=True) + '\n')
elif mode == 'check-plan':
    import re
    plan = Path(sys.argv[2]).read_text()
    if any(line.startswith('Remv ') or re.match(r'^Inst \S+ \[', line)
           for line in plan.splitlines()):
        raise SystemExit('Refusing any existing OS package upgrade/removal')
elif mode == 'compare':
    before = json.loads(Path(sys.argv[2]).read_text())
    after = inventory()
    changed = [name for name in before if after.get(name) != before[name]]
    if changed:
        raise SystemExit('Existing OS package changed: ' + ', '.join(changed))
    Path(sys.argv[3]).write_text(json.dumps(after, indent=2, sort_keys=True) + '\n')
    print('Existing OS packages unchanged; added=' + str(len(set(after) - set(before))))
else:
    raise SystemExit('Unsupported operation')
