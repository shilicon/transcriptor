// Actual handlers AND renderJob/renderChapters/renderTranscript, minimal synthetic DOM.
// No bootstrap, browser, network or real jobs. Observations are not pass assertions.
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.resolve(__dirname, '../../app/static/app.js'), 'utf8');
const marker = '(async function init() {';
if (!source.includes(marker)) throw new Error('bootstrap changed');

class Element {
  constructor(tag = 'div') {
    this.tag = tag; this.children = []; this.dataset = {}; this.style = {};
    this.value = ''; this.textContent = ''; this.className = ''; this.disabled = false;
    this.classList = {add(){}, remove(){}, toggle(){}};
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener() {}
  querySelectorAll(selector) {
    const match = node => selector.startsWith('.')
      ? node.className.split(' ').includes(selector.slice(1))
      : selector === 'p[data-index]' ? node.tag === 'p' && node.dataset.index != null
      : selector === 'select.speaker-select' ? node.tag === 'select' && node.className === 'speaker-select'
      : false;
    return this.children.flatMap(node => [ ...(match(node) ? [node] : []), ...node.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => {resolve=res; reject=rej;});
  return {promise, resolve, reject};
}
function job(text = 'original text', chapters = []) {
  return {id:'A', title:'Synthetic A', status:'done',
    paragraphs:[{start:0, text, speaker_id:'S1'}], speakers:[{id:'S1',name:'Synthetic'}], chapters};
}
function setup() {
  const roots = new Map(), calls = [], warnings = [], renders = [];
  const root = s => {if(!roots.has(s)) roots.set(s,new Element()); return roots.get(s);};
  const ctx = vm.createContext({
    document: {
      querySelector: root,
      querySelectorAll: s => {const [id,...rest] = s.split(' '); return root(id).querySelectorAll(rest.join(' '));},
      createElement: tag => new Element(tag), createTextNode: t => {const n=new Element('text');n.textContent=t;return n;},
    }, localStorage:{setItem(){},getItem(){return null;}},
    console:{warn:e=>warnings.push(String(e)),log:console.log}, setTimeout,clearTimeout,
  });
  vm.runInContext(source.slice(0,source.indexOf(marker)),ctx);
  ctx.recordRender = j => renders.push({text:j.paragraphs[0].text,chapters:j.chapters.length});
  vm.runInContext(`
    globalThis.probe = {state, saveTranscript, saveSpeakers, runAutoChapters, refreshJobState, toggleTranscriptEditing, renderJob};
    const actualRenderJob = renderJob;
    renderJob = job => { globalThis.recordRender(job); actualRenderJob(job); };
    renderJobs = () => {};
    switchTab = () => {};
    toast = () => {};
    api = (...args) => globalThis.mockAPI(...args);
  `,ctx);
  const p=ctx.probe;
  p.state.job=job();p.state.selectedJobId='A';p.state.editing=false;
  p.renderJob(p.state.job);
  const observe=()=>({state_text:p.state.job.paragraphs[0].text,
    state_chapters:p.state.job.chapters.length,
    rendered_chapter_rows:root('#chapter-editor').querySelectorAll('.chapter-row').length,
    editing:p.state.editing,
    draft_text:root('#transcript').querySelectorAll('p[data-index]')[0]?.textContent || null,
    chapter_busy:root('#auto-chapters').disabled,
    renders:[...renders],calls:[...calls],warnings:[...warnings]});
  return {ctx,p,root,calls,warnings,observe};
}
async function chapterFirst() {
  const h=setup(); const {ctx,p,root,calls}=h;
  const chapter=deferred(), text=deferred();let server=job();
  ctx.mockAPI=(url,opt={})=>{
    calls.push(`${opt.method||'GET'} ${url}`);
    if(url.endsWith('/chapters/auto')) return chapter.promise;
    if(url.endsWith('/paragraphs')) return text.promise;
    if(url==='/api/jobs/A') return Promise.resolve(structuredClone(server));
    throw Error(url);
  };
  const c=p.runAutoChapters();
  p.toggleTranscriptEditing();
  root('#transcript').querySelectorAll('p[data-index]')[0].textContent='newer saved text';p.state.draftVersion++;
  const t=p.saveTranscript();
  const oldSave=job('newer saved text');
  server=job('newer saved text',[{start:0,title:'Generated'}]);
  chapter.resolve(structuredClone(server));await c;
  const afterChapter=h.observe();
  text.resolve(oldSave);await t;
  const afterAll=h.observe();
  // Independent explicit refresh also uses the actual production function.
  await p.refreshJobState('A');
  const afterRefresh=h.observe();
  p.toggleTranscriptEditing();
  return {after_chapter_refresh:afterChapter,after_all_responses:afterAll,after_explicit_refresh:afterRefresh,
    after_exit_editing:h.observe(),server_chapters:server.chapters.length};
}
async function refreshWithDraft(failFirst) {
  const h=setup();const {ctx,p,root,calls}=h;const read=deferred();let count=0;
  ctx.mockAPI=(url,opt={})=>{calls.push(`${opt.method||'GET'} ${url}`);return ++count===1 ? read.promise : Promise.resolve(job('saved text',[{start:0,title:'Recovered'}]));};
  const task=p.refreshJobState('A');
  p.toggleTranscriptEditing();
  root('#transcript').querySelectorAll('p[data-index]')[0].textContent='unsaved during refresh';p.state.draftVersion++;
  if(failFirst) read.reject(Error('synthetic refresh failure'));
  else read.resolve(job('saved text',[{start:0,title:'Generated'}]));
  await task;
  const afterFirst=h.observe();
  if(failFirst) await p.refreshJobState('A');
  return {after_first:afterFirst,after_recovery:h.observe()};
}
(async()=>console.log(JSON.stringify({chapter_response_before_save:await chapterFirst(),
  draft_during_refresh:await refreshWithDraft(false),refresh_failure_recovery:await refreshWithDraft(true)},null,2)))()
  .catch(e=>{console.error(e);process.exitCode=1;});
