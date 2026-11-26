"""Independent audit probes. Only synthetic data; no external calls or audio models."""
import asyncio
import copy
import json
import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

source = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(source))
os.environ['TRANSCRIPTOR_DATA_DIR'] = tempfile.mkdtemp(prefix='transcriptor-audit-data-', dir='/private/tmp')
from fastapi.testclient import TestClient
from app import main, pipeline
from app.correction import _apply_edits
from app.formatting import build_paragraphs
from app.utils import save_job, load_job, job_dir

results = []
def record(name, **details):
    results.append({'probe': name, **details})

def fixture(jid, status='done'):
    return {'id': jid, 'title': 'Synthetic audit fixture', 'created_at': '2026-11-11T00:00:00+08:00',
            'status': status, 'progress': 100, 'settings': {'ai_correction': False, 'chapters': False, 'num_speakers': 1},
            'speaker_names': ['Audit speaker'], 'speakers': [{'id': 'S1', 'name': 'Audit speaker'}],
            'audio': {'filename': 'audit.wav', 'source_filename': 'input.wav', 'duration': 10},
            'paragraphs': [{'id': 'p0001', 'start': 0, 'end': 10, 'speaker_id': 'S1', 'text': 'Original approved text.', 'original_text': 'Original approved text.'}],
            'chapters': [], 'stats': {}}

# Standard English is changed even when AI correction is disabled.
text = 'We have decades of experience.'
paragraphs = build_paragraphs([{'start': 0, 'end': 10, 'text': text}], [0], 'en')
record('glossary_corruption', before=text, after=paragraphs[0]['text'])

# Deterministic counterexamples to correction invariants.
for label, text, original, replacement, kind in [
    ('number_order', 'We increased from 10 to 20 units.', 'from 10 to 20', 'from 20 to 10', 'homophone'),
    ('negation_deleted', 'This change is not safe for customers.', 'not safe', 'safe', 'duplicate'),
    ('chinese_rewrite', '我们认为这个方案不能上线，因为目前还有严重风险。', '我们认为这个方案不能上线，因为目前还有严重风险', '我们认为这个方案可以上线，因为目前已经没有风险', 'homophone'),
]:
    paragraph = {'text': text}
    applied, _ = _apply_edits(paragraph, [{'original': original, 'replacement': replacement, 'type': kind, 'confidence': .99}])
    record(label, applied=applied, before=text, after=paragraph['text'])

# A running worker continues after deletion and recreates the job directory.
save_job(fixture('delete-running', 'queued'))
entered, release = threading.Event(), threading.Event()
def fake_transcribe(**kwargs):
    entered.set()
    assert release.wait(10), 'test barrier timed out'
    return [{'start': 0, 'end': 10, 'text': 'Audit transcript.'}]
with patch.object(pipeline, 'probe_duration', return_value=10), patch.object(pipeline, 'extract_audio'), patch.object(pipeline, 'transcribe', side_effect=fake_transcribe), patch.object(pipeline, 'diarize_segments', return_value=([0], 1)):
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(pipeline.run_pipeline, 'delete-running')
        assert entered.wait(10), 'worker did not start'
        deleted = main.delete_job('delete-running')
        existed_after_delete = job_dir('delete-running').exists()
        release.set()
        future.result(10)
record('running_job_resurrected', delete_response=deleted, exists_immediately_after_delete=existed_after_delete,
       exists_after_worker=job_dir('delete-running').exists(), final_status=load_job('delete-running')['status'])

# Slow chapter generation saves a stale whole-job copy after an unrelated edit.
async def race_probe():
    save_job(fixture('race-edit'))
    entered, release = threading.Event(), threading.Event()
    def fake_llm(*args):
        entered.set()
        assert release.wait(10), 'test barrier timed out'
        return [{'start': 0, 'title': 'Generated chapter'}]
    with patch.object(main, 'auto_chapters_llm', side_effect=fake_llm):
        pending = asyncio.create_task(main.auto_chapters('race-edit', main.AutoChaptersRequest(endpoint='http://audit.invalid/v1', model='mock')))
        assert await asyncio.to_thread(entered.wait, 10), 'chapter worker did not start'
        edited = await asyncio.to_thread(main.update_paragraphs, 'race-edit', main.ParagraphsUpdate(texts=['Saved human correction.'], speaker_ids=['S1']))
        text_after_save = load_job('race-edit')['paragraphs'][0]['text']
        release.set()
        await pending
    record('chapter_generation_loses_edit', save_response=edited['paragraphs'][0]['text'], after_save=text_after_save,
           after_chapters=load_job('race-edit')['paragraphs'][0]['text'])
asyncio.run(race_probe())

# Undo also replaces a never-AI-corrected paragraph that the user edited.
job = fixture('undo-edit')
job['correction'] = {'enabled': True, 'status': 'applied', 'applied': 1}
job['paragraphs'].append({'id':'p0002', 'start':10, 'end':20, 'speaker_id':'S1', 'text':'Manual correction only.', 'original_text':'Wrong ASR text.'})
save_job(job)
main.undo_correction('undo-edit')
record('undo_erases_manual_edit', before='Manual correction only.', after=load_job('undo-edit')['paragraphs'][1]['text'])

# Inspect actual route behavior under hostile Host/Origin without real network.
with TestClient(main.app, raise_server_exceptions=False) as client:
    response = client.get('/api/jobs', headers={'Host': 'untrusted.invalid:8765', 'Origin': 'http://untrusted.invalid:8765'})
    record('untrusted_host_accepted', status=response.status_code, returned_jobs=len(response.json()))
    response = client.post('/api/jobs/undo-edit/correction/undo', headers={'Origin':'https://untrusted.invalid', 'Content-Type':'text/plain'}, content='')
    record('cross_origin_simple_mutation', status=response.status_code, correction_status=response.json().get('correction', {}).get('status'))
    with patch.object(main.executor, 'submit') as submit:
        response = client.post('/api/jobs', headers={'Origin':'https://untrusted.invalid'}, data={'ai_correction':'false','chapters':'false'}, files={'file':('audit.wav', b'synthetic-audio', 'audio/wav')})
        record('cross_origin_multipart_creation', status=response.status_code, worker_scheduled=submit.called)
    bad = job_dir('malformed')
    bad.mkdir()
    (bad / 'job.json').write_text('{broken', encoding='utf-8')
    response = client.get('/api/jobs')
    record('single_malformed_file_breaks_list', status=response.status_code)
    (bad / 'job.json').unlink()

# Single malformed job blocks lifespan startup, not just the list.
bad = job_dir('malformed')
(bad / 'job.json').write_text('{broken', encoding='utf-8')
try:
    with TestClient(main.app):
        pass
except Exception as exc:
    record('single_malformed_file_blocks_startup', exception=type(exc).__name__)

print(json.dumps({'data_directory': os.environ['TRANSCRIPTOR_DATA_DIR'], 'results': results}, ensure_ascii=False, indent=2))
