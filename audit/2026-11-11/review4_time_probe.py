"""Independent read/display-contract and chapter-write probes; synthetic data only."""
import contextlib
import hashlib
import json
import math
from pathlib import Path
import runpy
import sys

p = runpy.run_path(str(Path(__file__).with_name('reaudit_reproduce.py')))
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
from app.config import MAX_TIME_SECONDS

def hashes(folder):
    return {str(f.relative_to(folder)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in folder.rglob('*') if f.is_file()}

values = [('normal', 10.5), ('max', float(MAX_TIME_SECONDS)),
          ('above', math.nextafter(float(MAX_TIME_SECONDS), math.inf)),
          ('huge', 1e20), ('inf', math.inf), ('nan', math.nan), ('negative', -1)]
read_results = {}
with p['isolated_jobs_dir']() as (_, jobs):
    p['save_job'](p['make_job']('healthy'))
    originals = {}
    for kind in ['duration', 'paragraph', 'chapter', 'segments', 'raw_segments']:
        for label, value in values:
            job_id = kind + '-' + label
            job = p['make_job'](job_id)
            if kind == 'duration':
                job['audio']['duration'] = value
            elif kind == 'paragraph':
                job['paragraphs'][0].update(start=value, end=value)
            elif kind == 'chapter':
                job['chapters'] = [{'start': value, 'title': 'Synthetic'}]
            else:
                job[kind][0].update(start=value, end=value)
            p['save_job'](job)
            originals[job_id] = (jobs / job_id / 'job.json').read_bytes()
            try:
                p['load_job'](job_id)
                loader = 'accepted'
            except Exception as e:
                loader = type(e).__name__
            read_results[job_id] = {'loader': loader, 'expected_valid': label in ['normal', 'max']}
    legacy = p['make_job']('legacy')
    legacy['chapters'] = [{'start':'00:00', 'title':'Legacy'}, {'start':'01:30.5', 'title':'Later'}]
    p['save_job'](legacy)
    with contextlib.redirect_stdout(sys.stderr):
        with p['TestClient'](p['main'].app, base_url='http://127.0.0.1:8765') as client:
            headers = {p['security'].SESSION_HEADER: p['security'].TOKEN}
            listing = client.get('/api/jobs', headers=headers)
            assert listing.status_code == 200
            summaries = {j['id']:j for j in listing.json()}
            (out/'list.json').write_text(listing.text)
            for job_id, item in read_results.items():
                res = client.get('/api/jobs/'+job_id, headers=headers)
                item.update(detail_status=res.status_code, corrupt=summaries[job_id]['corrupt'],
                            original_file_unchanged=originals[job_id] == (jobs/job_id/'job.json').read_bytes())
                assert res.status_code == (200 if item['expected_valid'] else 422), (job_id, item)
                assert item['corrupt'] != item['expected_valid']
                assert item['original_file_unchanged']
                if res.status_code == 200:
                    (out/(job_id+'-detail.json')).write_text(res.text)
            for job_id in ['healthy', 'legacy']:
                res = client.get('/api/jobs/'+job_id, headers=headers)
                assert res.status_code == 200
                (out/(job_id+'-detail.json')).write_text(res.text)
            assert client.get('/api/jobs/legacy',headers=headers).json()['chapters'][1]['start'] == 90.5

write_results = {}
for label, value in [('max', MAX_TIME_SECONDS), ('above', MAX_TIME_SECONDS+1),
                     ('ui-time-string', '43200:01'), ('huge', 1e20)]:
    with p['isolated_jobs_dir']() as (_, jobs):
        p['save_job'](p['make_job']('editable'))
        p['save_job'](p['make_job']('healthy'))
        with contextlib.redirect_stdout(sys.stderr):
            with p['TestClient'](p['main'].app,base_url='http://127.0.0.1:8765',raise_server_exceptions=False) as client:
                headers = {p['security'].SESSION_HEADER:p['security'].TOKEN}
                seed = client.put('/api/jobs/editable/chapters',headers=headers,json={'chapters':[{'start':0,'title':'Original'}]})
                assert seed.status_code == 200
                before = hashes(jobs/'editable')
                request = {'chapters':[{'start':0,'title':'Opening'},{'start':value,'title':'Outside time range'}]}
                save = client.put('/api/jobs/editable/chapters',headers=headers,json=request)
                detail = client.get('/api/jobs/editable',headers=headers)
                listing = client.get('/api/jobs',headers=headers)
                summary = next(j for j in listing.json() if j['id']=='editable')
                healthy = client.get('/api/jobs/healthy',headers=headers)
                result = {'request':request, 'save_status':save.status_code, 'detail_status':detail.status_code,
                          'list_status':listing.status_code, 'corrupt':summary['corrupt'],
                          'job_and_exports_unchanged': before == hashes(jobs/'editable'),
                          'healthy_detail_status':healthy.status_code,
                          'saved_start':json.loads((jobs/'editable/job.json').read_text())['chapters'][-1]['start']}
                write_results[label] = result
                (out/('write-'+label+'-response.json')).write_text(save.text)
                (out/('write-'+label+'-detail.json')).write_text(detail.text)
print(json.dumps({'read_contract':read_results,'chapter_write_contract':write_results},indent=2,ensure_ascii=False))
