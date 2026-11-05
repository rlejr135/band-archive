"""Own-container wrapper: preserve runner status and verify post-run packages."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, '/verification-support')
import runtime_manifest

code = subprocess.run([sys.executable, '/verification-support/run_isolated.py',
                       *sys.argv[1:]]).returncode
output = Path('/tmp/verification-postrun')
output.mkdir(exist_ok=False)
manifest = runtime_manifest.capture()
path = output / 'runtime-final.json'
path.write_text(json.dumps(manifest, indent=2) + '\n')
comparison_code, reason = 0, None
try:
    before = json.loads(Path('/verification-evidence/runtime-before.json').read_text())
    runtime_manifest.compare(before, manifest, set())
except SystemExit as error:
    comparison_code, reason = 1, str(error)
status = {'runner_exit': code, 'manifest_comparison_exit': comparison_code,
          'manifest_error': reason,
          'runtime_final_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
(output / 'status.json').write_text(json.dumps(status, indent=2) + '\n')
print('POSTRUN_DIR=' + str(output), flush=True)
print('POSTRUN_STATUS=' + json.dumps(status), flush=True)
raise SystemExit(code if code != 0 else comparison_code)
