// Formal Web state-order regression. Uses actual app.js handlers with stubbed
// DOM/network/renderer; no browser or real backend required.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(
  path.resolve(__dirname, "../app/static/app.js"),
  "utf8",
);
const marker = "(async function init() {";
if (!source.includes(marker)) throw new Error("app.js bootstrap marker changed");

const controls = new Map();
function control(selector) {
  if (!controls.has(selector)) {
    controls.set(selector, {
      value: "",
      textContent: "",
      disabled: false,
      dataset: {},
      classList: { add() {}, remove() {}, toggle() {} },
    });
  }
  return controls.get(selector);
}

const transcriptDraft = {
  dataset: { index: "0" },
  textContent: "newer saved text",
};
const context = vm.createContext({
  document: {
    querySelector: control,
    querySelectorAll(selector) {
      if (selector === "#transcript p[data-index]") return [transcriptDraft];
      if (selector === "#speaker-inputs input[data-speaker-id]") return [];
      return [];
    },
  },
  localStorage: { setItem() {}, getItem() { return null; } },
  console,
  setTimeout,
  clearTimeout,
});

vm.runInContext(source.slice(0, source.indexOf(marker)), context);
vm.runInContext(
  `
  globalThis.probe = { state, saveTranscript, saveSpeakers, runAutoChapters };
  renderJob = job => {
    globalThis.lastRendered = {
      text: job.paragraphs && job.paragraphs[0] ? job.paragraphs[0].text : null,
      chapters: (job.chapters || []).length,
    };
  };
  renderJobs = () => {};
  setEditingUI = () => {};
  toast = () => {};
  switchTab = () => {};
  api = (...args) => globalThis.mockAPI(...args);
  `,
  context,
);

const p = context.probe;
function job(text, chapters = []) {
  return {
    id: "A",
    title: "Synthetic A",
    status: "done",
    paragraphs: [{ start: 0, text, speaker_id: "S1" }],
    speakers: [],
    chapters,
  };
}

(async () => {
  // Speaker save response arrives after a newer transcript save.
  p.state.job = job("original text");
  p.state.selectedJobId = "A";
  p.state.editing = true;
  p.state.mutationVersion = 0;
  p.state.speakerSaveRequest = 0;
  p.state.saveRequest = 0;
  p.state.draftVersion = 0;
  let releaseSpeaker;
  let currentText = "original text";
  context.mockAPI = (url) => {
    if (url.endsWith("/speakers")) {
      return new Promise((resolve) => {
        releaseSpeaker = resolve;
      });
    }
    if (url.endsWith("/paragraphs")) {
      currentText = "newer saved text";
      return Promise.resolve(job(currentText));
    }
    if (url.endsWith("/api/jobs/A")) return Promise.resolve(job(currentText));
    return Promise.resolve({});
  };

  const speakerSave = p.saveSpeakers();
  await p.saveTranscript();
  assert.equal(p.state.job.paragraphs[0].text, "newer saved text");
  releaseSpeaker(job("original text"));
  await speakerSave;
  const speakerResult = {
    before_old_response: "newer saved text",
    after_old_response: p.state.job.paragraphs[0].text,
    rendered_text: context.lastRendered.text,
  };
  assert.equal(p.state.job.paragraphs[0].text, "newer saved text");
  assert.equal(context.lastRendered.text, "newer saved text");

  // REVIEW-3 order: an older GET refresh starts during a save, then the newer
  // save response is accepted; the late stale GET must not roll the display back.
  p.state.job = job("original text");
  p.state.selectedJobId = "A";
  p.state.editing = true;
  p.state.mutationVersion = 0;
  p.state.snapshotVersion = 0;
  p.state.saveRequest = 0;
  p.state.speakerSaveRequest = 0;
  p.state.draftVersion = 0;
  let releaseSpeaker2;
  let releaseTranscript2;
  let releaseRefresh2;
  let refreshStarted2 = false;
  let server2 = job("original text");
  context.mockAPI = (url) => {
    if (url.endsWith("/speakers")) {
      return new Promise((resolve) => { releaseSpeaker2 = resolve; });
    }
    if (url.endsWith("/paragraphs")) {
      return new Promise((resolve) => { releaseTranscript2 = resolve; });
    }
    if (url.endsWith("/api/jobs/A")) {
      refreshStarted2 = true;
      return new Promise((resolve) => { releaseRefresh2 = resolve; });
    }
    return Promise.resolve({});
  };
  const speakerTask2 = p.saveSpeakers();
  const transcriptTask2 = p.saveTranscript();
  releaseSpeaker2(job("original text"));
  for (let i = 0; i < 100 && !refreshStarted2; i++) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  assert.equal(refreshStarted2, true);
  server2 = job("newer saved text");
  releaseTranscript2(structuredClone(server2));
  await transcriptTask2;
  const afterNewerSave2 = p.state.job.paragraphs[0].text;
  releaseRefresh2(job("original text"));
  await speakerTask2;
  assert.equal(p.state.job.paragraphs[0].text, "newer saved text");
  assert.equal(context.lastRendered.text, "newer saved text");
  const refreshResult = {
    after_save_response: afterNewerSave2,
    after_refresh_response: p.state.job.paragraphs[0].text,
    rendered_text: context.lastRendered.text,
    server_text: server2.paragraphs[0].text,
  };

  // Valid chapter completion overlaps a transcript save; stale response must
  // trigger a latest-state refresh rather than disappear.
  p.state.job = job("original text");
  p.state.selectedJobId = "A";
  p.state.editing = false;
  p.state.mutationVersion = 0;
  p.state.chapterRequest = 0;
  p.state.saveRequest = 0;
  p.state.draftVersion = 0;
  p.state.chapters = [];
  let releaseChapter;
  currentText = "chapter saved text";
  context.mockAPI = (url) => {
    if (url.endsWith("/chapters/auto")) {
      return new Promise((resolve) => {
        releaseChapter = resolve;
      });
    }
    if (url.endsWith("/paragraphs")) return Promise.resolve(job(currentText));
    if (url.endsWith("/api/jobs/A")) {
      return Promise.resolve(job(currentText, [{ start: 0, title: "Generated" }]));
    }
    return Promise.resolve({});
  };

  const chapterSave = p.runAutoChapters();
  await new Promise((resolve) => setTimeout(resolve, 0));
  await p.saveTranscript();
  releaseChapter(job(currentText, [{ start: 0, title: "Generated" }]));
  await chapterSave;
  const chapterResult = {
    displayed_chapters_after_success: p.state.job.chapters.length,
    rendered_chapters: context.lastRendered.chapters,
  };
  assert.equal(p.state.job.chapters.length, 1);
  assert.equal(context.lastRendered.chapters, 1);

  console.log(JSON.stringify({
    speaker_response_after_text_save: speakerResult,
    refresh_after_newer_save: refreshResult,
    chapter_during_text_save: chapterResult,
  }, null, 2));
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
