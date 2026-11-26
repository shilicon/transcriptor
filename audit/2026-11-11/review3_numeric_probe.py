"""Synthetic API fixtures for numeric presentation limits, with file-preservation checks."""
import contextlib
import json
from pathlib import Path
import runpy
import sys

prior = runpy.run_path(str(Path(__file__).with_name('reaudit_reproduce.py')))
main, security = prior['main'], prior['security']
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
results = {}
for name in ('duration', 'paragraph', 'chapter'):
    with prior['isolated_jobs_dir']() as (_, jobs):
        prior['save_job'](prior['make_job']('healthy'))
        job = prior['make_job']('damaged')
        if name == 'duration':
            job['audio']['duration'] = 1e20
        elif name == 'paragraph':
            job['paragraphs'][0]['start'] = 1e20
            job['paragraphs'][0]['end'] = 1e20
        else:
            job['chapters'] = [{'start': 1e20, 'title': 'Synthetic chapter'}]
        prior['save_job'](job)
        original = (jobs / 'damaged/job.json').read_bytes()
        try:
            prior['load_job']('damaged')
            loader = 'accepted'
        except Exception as exc:
            loader = type(exc).__name__
        with contextlib.redirect_stdout(sys.stderr):
            with prior['TestClient'](main.app, base_url='http://127.0.0.1:8765', raise_server_exceptions=False) as client:
                headers = {security.SESSION_HEADER: security.TOKEN}
                listing = client.get('/api/jobs', headers=headers)
                detail = client.get('/api/jobs/damaged', headers=headers)
                healthy = client.get('/api/jobs/healthy', headers=headers)
        (out / f'{name}-list.json').write_text(listing.text)
        (out / f'{name}-detail.json').write_text(detail.text)
        (out / 'healthy-detail.json').write_text(healthy.text)
        summary = next(item for item in listing.json() if item['id'] == 'damaged')
        results[name] = {'loader':loader, 'startup':'ok', 'list_status':listing.status_code,
                         'detail_status':detail.status_code, 'corrupt':summary['corrupt'],
                         'original_file_unchanged':original == (jobs / 'damaged/job.json').read_bytes()}
print(json.dumps(results, indent=2))
