// Independent observations of actual Web handlers. Synthetic network and DOM only.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.resolve(__dirname, '../../app/static/app.js'), 'utf8');
const marker = '(async function init() {';
if (!source.includes(marker)) throw new Error('bootstrap marker changed');
const controls = new Map();
const draft = {dataset: {index: '0'}, textContent: 'newer saved text'};
const context = vm.createContext({
  document: {
    querySelector: selector => {
      if (!controls.has(selector)) controls.set(selector, {
        value: '', textContent: '', disabled: false, dataset: {},
        classList: {add(){}, remove(){}, toggle(){}},
      });
      return controls.get(selector);
    },
    querySelectorAll: selector => selector === '#transcript p[data-index]' ? [draft] : [],
  },
  localStorage: {setItem(){}, getItem(){return null;}},
  console, setTimeout, clearTimeout,
});
vm.runInContext(source.slice(0, source.indexOf(marker)), context);
vm.runInContext(`
  globalThis.probe = {state, saveTranscript, saveSpeakers};
  renderJob = job => { globalThis.lastRenderedText = job.paragraphs[0].text; };
  renderJobs = () => {};
  setEditingUI = () => {};
  toast = () => {};
  api = (...args) => globalThis.mockAPI(...args);
`, context);
const p = context.probe;
const job = text => ({id:'A', title:'Synthetic A', status:'done',
  paragraphs:[{start:0, text, speaker_id:'S1'}], speakers:[], chapters:[]});
function deferred() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return {promise, resolve};
}

(async () => {
  p.state.job = job('original text');
  p.state.selectedJobId = 'A';
  p.state.editing = true;
  const speaker = deferred(), transcript = deferred(), refresh = deferred(), refreshStarted = deferred();
  let server = job('original text');
  let refreshSnapshot;
  const calls = [];
  context.mockAPI = (url, options = {}) => {
    calls.push(`${options.method || 'GET'} ${url}`);
    if (url.endsWith('/speakers')) return speaker.promise;
    if (url.endsWith('/paragraphs')) return transcript.promise;
    if (url === '/api/jobs/A') {
      refreshSnapshot = structuredClone(server);
      refreshStarted.resolve();
      return refresh.promise;
    }
    throw new Error(`unexpected request ${url}`);
  };
  const speakerTask = p.saveSpeakers();
  const transcriptTask = p.saveTranscript();
  // Speaker response is stale by request generation; it initiates a refresh.
  // The newer transcript request has started, but has not committed yet.
  speaker.resolve(job('original text'));
  await refreshStarted.promise;
  // GET has captured the old server snapshot. Its response is held in transit.
  server = job('newer saved text');
  transcript.resolve(structuredClone(server));
  await transcriptTask;
  const afterSave = p.state.job.paragraphs[0].text;
  refresh.resolve(refreshSnapshot);
  await speakerTask;
  console.log(JSON.stringify({refresh_after_newer_save: {
    calls,
    after_save_response: afterSave,
    after_refresh_response: p.state.job.paragraphs[0].text,
    rendered_text: context.lastRenderedText,
    server_text: server.paragraphs[0].text,
    editing: p.state.editing,
    all_requests_completed: true,
  }}, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
