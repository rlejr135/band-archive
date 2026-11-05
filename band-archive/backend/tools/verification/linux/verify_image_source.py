import hashlib
import json
from pathlib import Path

source = Path('/verification/source/backend')
image = Path('/app')
evidence = {}
missing, changed = [], []
for path in sorted(source.rglob('*.py')):
    relative = path.relative_to(source)
    if relative.parts[0] in {'tests', 'tools'}:
        continue
    counterpart = image / relative
    if not counterpart.is_file():
        missing.append(relative.as_posix())
        continue
    baseline, deployed = path.read_bytes(), counterpart.read_bytes()
    expected = hashlib.sha256(baseline).hexdigest()
    actual = hashlib.sha256(deployed).hexdigest()
    normalized_expected = hashlib.sha256(baseline.replace(b'\r\n', b'\n')).hexdigest()
    normalized_actual = hashlib.sha256(deployed.replace(b'\r\n', b'\n')).hexdigest()
    if normalized_expected != normalized_actual:
        changed.append(relative.as_posix())
    evidence[relative.as_posix()] = {'baseline_raw': expected, 'image_raw': actual,
                                   'baseline_eol_normalized': normalized_expected,
                                   'image_eol_normalized': normalized_actual}
Path('/verification-evidence/product-source.json').write_text(
    json.dumps({'files': evidence, 'missing': missing, 'changed': changed}, indent=2) + '\n')
if missing or changed:
    raise SystemExit('Product/image source mismatch: missing=' + str(missing) + ' changed=' + str(changed))
print('Baseline product Python sources match deployment image after CRLF-to-LF normalization: '
      + str(len(evidence)) + '; raw EOL differences='
      + str(sum(item['baseline_raw'] != item['image_raw'] for item in evidence.values())))
