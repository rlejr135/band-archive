"""Match real JUnit cases to separately preserved collection node IDs."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--collection', required=True, type=Path)
parser.add_argument('--execution', required=True, type=Path)
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
collected = [line.strip() for line in args.collection.read_text(encoding='utf-8').splitlines()
             if line.startswith('tests/') and '::' in line]
modules = {node.split('::', 1)[0][:-3].replace('/', '.'): node.split('::', 1)[0]
           for node in collected}
results = []
for case in ET.parse(args.execution).getroot().iter('testcase'):
    classname, name = case.get('classname', ''), case.get('name', '')
    matching = [module for module in modules
                if classname == module or classname.startswith(module + '.')]
    if not matching:
        raise SystemExit('JUnit case has no matching collected module: ' + classname)
    module = max(matching, key=len)
    suffix = classname[len(module):].lstrip('.').replace('.', '::')
    node = modules[module] + '::' + (suffix + '::' if suffix else '') + name
    if node not in collected:
        raise SystemExit('JUnit case was not collected: ' + node)
    status = ('error' if case.find('error') is not None else
              'failed' if case.find('failure') is not None else
              'skipped' if case.find('skipped') is not None else 'passed')
    results.append({'node': node, 'status': status, 'seconds': float(case.get('time', '0'))})
counts = Counter(item['status'] for item in results)
executed = [item['node'] for item in results]
if len(executed) != len(set(executed)):
    raise SystemExit('Duplicate JUnit node; do not double count retry evidence')
summary = {'collected': len(collected), 'executed': len(results), 'counts': dict(counts),
           'missing': sorted(set(collected) - set(executed)), 'results': results,
           'collection_sha256': hashlib.sha256(args.collection.read_bytes()).hexdigest(),
           'junit_sha256': hashlib.sha256(args.execution.read_bytes()).hexdigest()}
with args.output.open('x', encoding='utf-8') as stream:
    json.dump(summary, stream, indent=2)
    stream.write('\n')
print(json.dumps({key: value for key, value in summary.items() if key != 'results'}))
