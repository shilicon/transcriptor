"""Run the real Swift formatters in disposable child processes; capture expected failures.

Arguments: compiled Review3FormatProbe executable, numeric fixture directory, output directory.
This does not start the native App. A nonzero child exit is an observation, not a passing test.
"""
import json
import os
from pathlib import Path
import resource
import subprocess
import sys

executable, fixtures, output = map(Path, sys.argv[1:4])
output.mkdir(parents=True, exist_ok=True)
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
results = {}
for mode in ['healthy', 'duration', 'paragraph', 'chapter']:
    name = 'duration-list.json' if mode == 'duration' else mode + '-detail.json'
    proc = subprocess.run([str(executable.resolve()), mode, str(fixtures / name)],
                          capture_output=True, text=True, timeout=20,
                          env=dict(os.environ, SWIFT_BACKTRACE='enable=no'))
    (output / f'format-{mode}.log').write_text(proc.stdout + proc.stderr)
    results[mode] = {'exit_code': proc.returncode, 'stdout': proc.stdout.strip(),
                     'diagnostic': proc.stderr.splitlines()[0] if proc.stderr else ''}
(output / 'format-results.json').write_text(json.dumps(results, indent=2, ensure_ascii=False) + '\n')
print(json.dumps(results, indent=2, ensure_ascii=False))
