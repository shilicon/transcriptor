const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const state = {
  job: null,
  jobs: [],
  timer: null,
  config: null,
  editing: false,
};

const LLM_ENDPOINT_KEY = "transcriptor.llm.endpoint";
const LLM_MODEL_KEY = "transcriptor.llm.model";
const TITLE_LANG_KEY = "transcriptor.chapter.language";

function formatTs(seconds) {
  if (seconds === null || seconds === undefined) return "00:00";
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const m = Math.floor(total / 60).toString().padStart(2, "0");
  const s = (total % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "未知时长";
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${h} 小时 ${m} 分 ${s} 秒`;
  if (m) return `${m} 分 ${s} 秒`;
  return `${s} 秒`;
}

function statusLabel(status) {
  return ({
    queued: "排队中",
    running: "处理中",
    done: "已完成",
    error: "失败",
  })[status] || status || "未知";
}

function toast(message, type = "") {
  const el = $("#toast");
  el.textContent = message;
  el.className = `toast ${type}`.trim();
  el.classList.remove("hidden");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => el.classList.add("hidden"), 5200);
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!response.ok) {
    const detail = data && data.detail ? data.detail : (typeof data === "string" ? data : `HTTP ${response.status}`);
    throw new Error(detail);
  }
  return data;
}

async function loadConfig() {
  try {
    const config = await api("/api/config");
    state.config = config;
    const modelSelect = $("#model");
    for (const option of Array.from(modelSelect.options)) {
      const info = config.whisper_models.find((item) => item.key === option.value);
      if (info && !info.available) {
        option.disabled = true;
        option.textContent += "（未安装）";
      }
    }
    const endpoint = localStorage.getItem(LLM_ENDPOINT_KEY) || config.llm_base_url || "";
    const model = localStorage.getItem(LLM_MODEL_KEY) || config.llm_model || "";
    $("#llm-endpoint").value = endpoint;
    $("#llm-model").value = model;
    $("#title-language").value = localStorage.getItem(TITLE_LANG_KEY) || "zh";
  } catch (error) {
    console.warn(error);
  }
}

function renderJobs() {
  const list = $("#job-list");
  list.replaceChildren();
  if (!state.jobs.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "暂无任务";
    list.append(empty);
    return;
  }
  for (const job of state.jobs) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "job-item" + (state.job && state.job.id === job.id ? " active" : "");
    button.addEventListener("click", () => selectJob(job.id));

    const top = document.createElement("div");
    top.style.display = "flex";
    top.style.alignItems = "center";
    top.style.justifyContent = "space-between";
    top.style.gap = "8px";

    const title = document.createElement("span");
    title.className = "job-item-title";
    title.textContent = job.title || "未命名任务";

    const pill = document.createElement("span");
    pill.className = `status-pill ${job.status || ""}`;
    pill.textContent = statusLabel(job.status);
    top.append(title, pill);

    const meta = document.createElement("div");
    meta.className = "job-item-meta";
    const bits = [];
    if (job.duration) bits.push(formatDuration(job.duration));
    if (job.stats && job.stats.paragraphs) bits.push(`${job.stats.paragraphs} 段`);
    if (job.created_at) {
      try { bits.push(new Date(job.created_at).toLocaleDateString("zh-CN")); } catch {}
    }
    meta.textContent = bits.join(" · ") || "—";

    button.append(top, meta);
    list.append(button);
  }
}

async function loadJobs() {
  try {
    state.jobs = await api("/api/jobs");
    renderJobs();
  } catch (error) {
    console.warn(error);
  }
}

function stopPolling() {
  if (state.timer) {
    clearTimeout(state.timer);
    state.timer = null;
  }
}

async function selectJob(id) {
  stopPolling();
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
    state.job = job;
    state.editing = false;
    setEditingUI();
    history.replaceState(null, "", `#job=${encodeURIComponent(id)}`);
    renderJob(job);
    renderJobs();
    if (job.status === "queued" || job.status === "running") {
      pollJob(id);
    }
  } catch (error) {
    toast(error.message, "error");
  }
}

async function pollJob(id) {
  stopPolling();
  const tick = async () => {
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
      if (!state.job || state.job.id !== id) return;
      state.job = job;
      renderJob(job);
      renderJobs();
      if (job.status === "queued" || job.status === "running") {
        state.timer = setTimeout(tick, 1200);
      } else {
        await loadJobs();
      }
    } catch (error) {
      console.warn(error);
      state.timer = setTimeout(tick, 2500);
    }
  };
  state.timer = setTimeout(tick, 700);
}

function renderJob(job) {
  $("#empty-state").classList.add("hidden");
  $("#job-view").classList.remove("hidden");

  $("#job-title").textContent = job.title || "未命名任务";
  $("#job-status").textContent = statusLabel(job.status);
  const meta = [];
  if (job.audio && job.audio.duration) meta.push(formatDuration(job.audio.duration));
  if (job.speakers && job.speakers.length) meta.push(`${job.speakers.length} 位说话人`);
  if (job.stats && job.stats.paragraphs) meta.push(`${job.stats.paragraphs} 个段落`);
  if (job.settings && job.settings.model) meta.push(job.settings.model);
  $("#job-meta").textContent = meta.join(" · ");

  const done = job.status === "done";
  $("#progress-card").classList.toggle("hidden", done || job.status === "error");
  if (!done && job.status !== "error") {
    $("#stage").textContent = job.stage || "处理中";
    $("#progress-text").textContent = `${Math.round(job.progress || 0)}%`;
    $("#progress-bar").style.width = `${Math.max(0, Math.min(100, job.progress || 0))}%`;
  }

  const exportWarning = $("#export-warning");
  if (exportWarning) {
    exportWarning.classList.toggle("hidden", !job.export_error);
    if (job.export_error) $("#export-warning-text").textContent = job.export_error;
  }

  const errorCard = $("#error-card");
  errorCard.classList.toggle("hidden", job.status !== "error");
  if (job.status === "error") {
    $("#error-text").textContent = job.error || "未知错误";
  }

  $("#result").classList.toggle("hidden", !done);
  if (done) {
    $("#dl-md").href = `/api/jobs/${encodeURIComponent(job.id)}/export.md`;
    $("#dl-docx").href = `/api/jobs/${encodeURIComponent(job.id)}/export.docx`;
    $("#dl-srt").href = `/api/jobs/${encodeURIComponent(job.id)}/export.srt`;
    $("#dl-vtt").href = `/api/jobs/${encodeURIComponent(job.id)}/export.vtt`;
    renderSpeakers(job);
    setEditingUI();
    renderTranscript(job);
    renderChapters(job);
  }
}

function renderSpeakers(job) {
  const container = $("#speaker-inputs");
  container.replaceChildren();
  const speakers = job.speakers || [];
  if (!speakers.length) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "没有识别到说话人。";
    container.append(p);
    return;
  }
  for (const speaker of speakers) {
    const wrapper = document.createElement("div");
    wrapper.className = "speaker-field";
    const label = document.createElement("label");
    const dot = document.createElement("span");
    dot.className = "speaker-dot";
    dot.style.background = speaker.color || "#0f766e";
    label.append(dot, document.createTextNode(speaker.id));
    const input = document.createElement("input");
    input.type = "text";
    input.value = speaker.name || "";
    input.dataset.speakerId = speaker.id;
    wrapper.append(label, input);
    container.append(wrapper);
  }
}

function buildBlocks(job) {
  const paragraphs = (job.paragraphs || []).map((p, index) => ({ ...p, _index: index }));
  const chapters = [...(job.chapters || [])].sort((a, b) => Number(a.start) - Number(b.start));
  if (!chapters.length) chapters.push({ start: 0, title: "全文" });
  const blocks = [];
  for (let i = 0; i < chapters.length; i++) {
    const start = Number(chapters[i].start) || 0;
    const end = i + 1 < chapters.length ? Number(chapters[i + 1].start) : Infinity;
    let items = paragraphs.filter((p) => Number(p.start) >= start && Number(p.start) < end);
    if (!items.length && i === 0) {
      items = paragraphs.filter((p) => Number(p.start) < end);
    }
    if (items.length) {
      blocks.push({ title: chapters[i].title || `第 ${i + 1} 部分`, items });
    }
  }
  if (!blocks.length && paragraphs.length) {
    blocks.push({ title: "全文", items: paragraphs });
  }
  return blocks;
}

function renderTranscript(job) {
  const container = $("#transcript");
  container.replaceChildren();
  const speakers = Object.fromEntries((job.speakers || []).map((s) => [s.id, s]));
  const blocks = buildBlocks(job);
  if (!blocks.length) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "没有可显示的逐字稿。";
    container.append(p);
    return;
  }
  blocks.forEach((block, blockIndex) => {
    const section = document.createElement("section");
    section.className = "chapter-block";
    const heading = document.createElement("h3");
    const num = document.createElement("span");
    num.className = "chapter-num";
    num.textContent = String(blockIndex + 1).padStart(2, "0");
    heading.append(num, document.createTextNode(block.title));
    section.append(heading);

    for (const paragraph of block.items) {
      const line = document.createElement("div");
      line.className = "line";
      const speakerLine = document.createElement("div");
      speakerLine.className = "speaker-line";
      const speaker = speakers[paragraph.speaker_id] || {};
      if (state.editing) {
        const select = document.createElement("select");
        select.className = "speaker-select";
        select.dataset.index = String(paragraph._index);
        for (const optionSpeaker of job.speakers || []) {
          const option = document.createElement("option");
          option.value = optionSpeaker.id;
          option.textContent = optionSpeaker.name;
          option.selected = optionSpeaker.id === paragraph.speaker_id;
          select.append(option);
        }
        speakerLine.append(select);
      } else {
        const name = document.createElement("span");
        name.style.color = speaker.color || "#0f766e";
        name.textContent = speaker.name || paragraph.speaker_name || "说话人";
        speakerLine.append(name);
      }
      const ts = document.createElement("span");
      ts.className = "ts";
      ts.textContent = formatTs(paragraph.start);
      speakerLine.append(ts);
      const text = document.createElement("p");
      text.textContent = paragraph.text || "";
      if (state.editing) {
        text.contentEditable = "true";
        text.dataset.index = String(paragraph._index);
        text.classList.add("editable");
      }
      line.append(speakerLine, text);
      section.append(line);
    }
    container.append(section);
  });
}

function setEditingUI() {
  const edit = $("#edit-transcript");
  const save = $("#save-transcript");
  if (!edit || !save) return;
  edit.textContent = state.editing ? "退出编辑" : "编辑文字";
  save.classList.toggle("hidden", !state.editing);
}

function toggleTranscriptEditing() {
  if (!state.job) return;
  state.editing = !state.editing;
  setEditingUI();
  renderTranscript(state.job);
}

async function saveTranscript() {
  if (!state.job) return;
  const texts = (state.job.paragraphs || []).map((p) => p.text || "");
  const speakerIds = (state.job.paragraphs || []).map((p) => p.speaker_id || "");
  document.querySelectorAll("#transcript p[data-index]").forEach((el) => {
    const index = Number(el.dataset.index);
    if (Number.isInteger(index) && index >= 0 && index < texts.length) {
      texts[index] = el.textContent.trim();
    }
  });
  document.querySelectorAll("#transcript select.speaker-select").forEach((el) => {
    const index = Number(el.dataset.index);
    if (Number.isInteger(index) && index >= 0 && index < speakerIds.length) {
      speakerIds[index] = el.value;
    }
  });
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(state.job.id)}/paragraphs`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texts, speaker_ids: speakerIds }),
    });
    state.job = job;
    state.editing = false;
    setEditingUI();
    renderJob(job);
    toast("逐字稿已保存");
  } catch (error) {
    toast(error.message, "error");
  }
}

function chapterRows() {
  return Array.from($("#chapter-editor").querySelectorAll(".chapter-row")).map((row) => ({
    start: row.querySelector(".chapter-start").value.trim(),
    title: row.querySelector(".chapter-title").value.trim(),
  })).filter((item) => item.title);
}

function makeChapterRow(chapter, index) {
  const row = document.createElement("div");
  row.className = "chapter-row";

  const number = document.createElement("div");
  number.className = "chapter-index";
  number.textContent = String(index + 1).padStart(2, "0");

  const start = document.createElement("input");
  start.className = "chapter-start";
  start.type = "text";
  start.value = formatTs(chapter.start);
  start.placeholder = "00:00";

  const title = document.createElement("input");
  title.className = "chapter-title";
  title.type = "text";
  title.value = chapter.title || "";
  title.placeholder = "章节标题";

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "remove-chapter";
  remove.title = "删除章节";
  remove.textContent = "×";
  remove.addEventListener("click", () => {
    row.remove();
    renumberChapterRows();
  });

  row.append(number, start, title, remove);
  return row;
}

function renumberChapterRows() {
  $$("#chapter-editor .chapter-row").forEach((row, index) => {
    row.querySelector(".chapter-index").textContent = String(index + 1).padStart(2, "0");
  });
}

function renderChapters(job) {
  const container = $("#chapter-editor");
  container.replaceChildren();
  const chapters = [...(job.chapters || [])].sort((a, b) => Number(a.start) - Number(b.start));
  chapters.forEach((chapter, index) => container.append(makeChapterRow(chapter, index)));
  if (!chapters.length) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "还没有章节。可以添加章节，或使用上面的自动分章。";
    container.append(p);
  }
}

async function saveSpeakers() {
  if (!state.job) return;
  const speakers = $$("#speaker-inputs input[data-speaker-id]").map((input) => ({
    id: input.dataset.speakerId,
    name: input.value.trim(),
  }));
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(state.job.id)}/speakers`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ speakers }),
    });
    state.job = job;
    renderJob(job);
    toast("说话人姓名已保存");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function saveChapters() {
  if (!state.job) return;
  const chapters = chapterRows();
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(state.job.id)}/chapters`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chapters }),
    });
    state.job = job;
    renderJob(job);
    toast("章节已保存");
  } catch (error) {
    toast(error.message, "error");
  }
}

function addChapter() {
  const container = $("#chapter-editor");
  const empty = container.querySelector("p.muted");
  if (empty) empty.remove();
  const rows = container.querySelectorAll(".chapter-row");
  let start = 0;
  if (rows.length) {
    const last = rows[rows.length - 1].querySelector(".chapter-start").value;
    const parts = last.split(":").map(Number);
    start = (parts.length === 2 ? parts[0] * 60 + parts[1] : 0) + 300;
  }
  container.append(makeChapterRow({ start, title: "新章节" }, rows.length));
}

async function runAutoChapters() {
  if (!state.job) return;
  const endpoint = $("#llm-endpoint").value.trim();
  const model = $("#llm-model").value.trim();
  const titleLanguage = $("#title-language").value;
  localStorage.setItem(LLM_ENDPOINT_KEY, endpoint);
  localStorage.setItem(LLM_MODEL_KEY, model);
  localStorage.setItem(TITLE_LANG_KEY, titleLanguage);
  const button = $("#auto-chapters");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "生成中…";
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(state.job.id)}/chapters/auto`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint, model, title_language: titleLanguage }),
    });
    state.job = job;
    renderJob(job);
    switchTab("chapters");
    toast("AI 章节已生成");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function runBasicChapters() {
  if (!state.job) return;
  const button = $("#basic-chapters");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "生成中…";
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(state.job.id)}/chapters/basic`, { method: "POST" });
    state.job = job;
    renderJob(job);
    switchTab("chapters");
    toast("已生成基础章节");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function deleteCurrentJob() {
  if (!state.job) return;
  if (!confirm(`删除“${state.job.title}”？音频和导出文件也会一起删除。`)) return;
  try {
    await api(`/api/jobs/${encodeURIComponent(state.job.id)}`, { method: "DELETE" });
    stopPolling();
    state.job = null;
    $("#job-view").classList.add("hidden");
    $("#empty-state").classList.remove("hidden");
    history.replaceState(null, "", location.pathname);
    await loadJobs();
    toast("任务已删除");
  } catch (error) {
    toast(error.message, "error");
  }
}

function switchTab(name) {
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
  $("#tab-transcript").classList.toggle("hidden", name !== "transcript");
  $("#tab-chapters").classList.toggle("hidden", name !== "chapters");
}

function setupUpload() {
  const fileInput = $("#file");
  const dropzone = $("#dropzone");
  const label = $("#file-label");

  fileInput.addEventListener("change", () => {
    if (fileInput.files.length) {
      label.textContent = fileInput.files[0].name;
    }
  });

  ["dragenter", "dragover"].forEach((event) => {
    dropzone.addEventListener(event, (e) => {
      e.preventDefault();
      dropzone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((event) => {
    dropzone.addEventListener(event, (e) => {
      e.preventDefault();
      dropzone.classList.remove("dragover");
    });
  });
  dropzone.addEventListener("drop", (e) => {
    if (e.dataTransfer.files.length) {
      fileInput.files = e.dataTransfer.files;
      label.textContent = e.dataTransfer.files[0].name;
    }
  });

  $("#upload-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const youtubeUrl = $("#youtube-url").value.trim();
    if (!youtubeUrl && !fileInput.files.length) {
      toast("请选择音频文件，或粘贴 YouTube 链接", "error");
      return;
    }

    const button = $("#start-btn");
    const original = button.textContent;
    button.disabled = true;
    button.textContent = youtubeUrl ? "下载中…" : "上传中…";
    try {
      let result;
      if (youtubeUrl) {
        const payload = {
          url: youtubeUrl,
          title: $("#title").value.trim(),
          speakers: $("#speakers").value.trim(),
          num_speakers: Number($("#num-speakers").value),
          language: $("#language").value,
          model: $("#model").value,
          initial_prompt: $("#initial-prompt").value.trim(),
        };
        result = await api("/api/jobs/url", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } else {
        const form = new FormData();
        form.append("file", fileInput.files[0]);
        form.append("title", $("#title").value.trim());
        form.append("speakers", $("#speakers").value.trim());
        form.append("num_speakers", $("#num-speakers").value);
        form.append("language", $("#language").value);
        form.append("model", $("#model").value);
        form.append("initial_prompt", $("#initial-prompt").value.trim());
        result = await api("/api/jobs", { method: "POST", body: form });
      }
      toast("任务已创建，开始转写");
      await loadJobs();
      await selectJob(result.id);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  });
}

function setupEvents() {
  $("#save-speakers").addEventListener("click", saveSpeakers);
  $("#edit-transcript").addEventListener("click", toggleTranscriptEditing);
  $("#save-transcript").addEventListener("click", saveTranscript);
  $("#save-chapters").addEventListener("click", saveChapters);
  $("#add-chapter").addEventListener("click", addChapter);
  $("#auto-chapters").addEventListener("click", runAutoChapters);
  $("#basic-chapters").addEventListener("click", runBasicChapters);
  $("#delete-job").addEventListener("click", deleteCurrentJob);
  $("#refresh-jobs").addEventListener("click", loadJobs);
  $$(".tab").forEach((tab) => tab.addEventListener("click", () => switchTab(tab.dataset.tab)));
  $("#llm-endpoint").addEventListener("change", (e) => localStorage.setItem(LLM_ENDPOINT_KEY, e.target.value.trim()));
  $("#llm-model").addEventListener("change", (e) => localStorage.setItem(LLM_MODEL_KEY, e.target.value.trim()));
  $("#title-language").addEventListener("change", (e) => localStorage.setItem(TITLE_LANG_KEY, e.target.value));
}

(async function init() {
  setupUpload();
  setupEvents();
  await loadConfig();
  await loadJobs();
  const params = new URLSearchParams(location.hash.replace(/^#/, ""));
  const jobId = params.get("job");
  if (jobId) {
    await selectJob(jobId);
    if (params.get("tab") === "chapters") switchTab("chapters");
  }
})();
