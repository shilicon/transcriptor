// Runs actual app.js mutation handlers in Node; network/rendering are controlled substitutes.
// This observes state snapshots, not a full browser or visual test.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.resolve(__dirname, '../../app/static/app.js'), 'utf8');
const marker = '(async function init() {';
if (!source.includes(marker)) throw new Error('app.js bootstrap marker changed');
const controls = new Map();
function control(selector) {
  if (!controls.has(selector)) controls.set(selector, {
    value: '', textContent: '', disabled: false, dataset: {},
    classList: {add(){}, remove(){}, toggle(){}},
  });
  return controls.get(selector);
}
const draft = {dataset: {index: '0'}, textContent: 'newer saved text'};
const context = vm.createContext({
  document: {querySelector: control, querySelectorAll: s => s === '#transcript p[data-index]' ? [draft] : []},
  localStorage: {setItem(){}, getItem(){return null;}},
  console, setTimeout, clearTimeout,
});
vm.runInContext(source.slice(0, source.indexOf(marker)), context);
vm.runInContext(`
  globalThis.probe = {state, saveTranscript, saveSpeakers, runAutoChapters};
  // Keep actual async mutation handlers; replace only unrelated UI and I/O.
  renderJob = job => { globalThis.lastRenderedText = job.paragraphs[0].text; };
  setEditingUI = () => {};
  toast = () => {};
  switchTab = () => {};
  api = (...args) => globalThis.mockAPI(...args);
`, context);
const p = context.probe;
const job = text => ({id: 'A', title: 'Synthetic A', status: 'done', paragraphs: [{start:0, text, speaker_id:'S1'}], speakers:[], chapters:[]});

(async () => {
  p.state.job = job('original text'); p.state.selectedJobId = 'A'; p.state.editing = true;
  let release;
  context.mockAPI = url => url.endsWith('/speakers')
    ? new Promise(resolve => { release = resolve; })
    : Promise.resolve(job('newer saved text'));
  const nameSave = p.saveSpeakers();
  await p.saveTranscript();
  const before = p.state.job.paragraphs[0].text;
  // Speaker update committed before the later transcript save, but its response is delayed.
  release(job('original text'));
  await nameSave;
  const result = {speaker_response_after_text_save: {
    before_old_response: before,
    after_old_response: p.state.job.paragraphs[0].text,
    rendered_text: context.lastRenderedText,
  }};
  console.log(JSON.stringify(result, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
