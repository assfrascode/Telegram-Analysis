const STORAGE_TOKEN = "chat_analyse_token";
const STORAGE_JOB = "chat_analyse_current_job";

const MAX_EVENTS = 300;
const UI_RENDER_THROTTLE_MS = 80;
const UPLOAD_PROGRESS_THROTTLE_MS = 140;

const ui = {
  renderScheduled: false,
  renderLog: false,
  renderStages: false,
  lastRenderAt: 0,
  jobStatusRefreshTimer: null,
  jobsRefreshTimer: null,
  capacityRefreshTimer: null,
  lastUploadProgressAt: 0,
};

const state = {
  token: sessionStorage.getItem(STORAGE_TOKEN),
  currentJobId: sessionStorage.getItem(STORAGE_JOB),
  ws: null,
  wsReconnectTimer: null,
  pollTimer: null,
  lastEventId: 0,
  events: [],
  eventIds: new Set(),
  capacity: null,
  jobs: [],
  questionSets: [],
  currentQuestionSetId: null,
  currentJob: null,
  deadLetters: [],
  uploadInProgress: false,
  downloadInProgress: false,
  mainMode: sessionStorage.getItem(STORAGE_JOB) ? "monitor" : "create",
  authMode: "login",
};

const STAGES = [
  {key: "upload", label: "Upload", events: ["upload.completed"]},
  {key: "validate", label: "ZIP-Prüfung", events: ["zip.scan.started", "zip.scan.completed"]},
  {key: "extract", label: "Extraktion", events: ["zip.extract.started", "zip.extract.completed"]},
  {key: "parse", label: "Telegram-Parsing", events: ["telegram.parse.started", "telegram.parse.progress", "telegram.parse.completed"]},
  {key: "media", label: "Medienanalyse", events: ["media.analysis.started", "media.analysis.progress", "media.analysis.completed"]},
  {
    key: "transcription",
    label: "Transkription",
    events: [
      "media.transcription.started",
      "media.transcription.progress",
      "media.transcription.completed",
    ],
  },
  {
    key: "translation",
    label: "Inhalte ins Englische übersetzen",
    events: [
      "translation.started",
      "translation.progress",
      "translation.completed",
      "translation.failed",
    ],
  },
  {key: "chunk", label: "Chunking", events: ["chunking.started", "chunking.progress", "chunking.completed"]},
  {key: "embedding", label: "Embedding", events: ["embedding.started", "embedding.progress", "embedding.completed"]},
  {key: "retrieval", label: "Retrieval", events: ["retrieval.started", "retrieval.progress", "retrieval.completed"]},
  {key: "reranking", label: "Reranking", events: ["reranking.started", "reranking.progress", "reranking.completed"]},
  {key: "answers", label: "Antworten", events: ["answer.started", "answer.progress", "answer.completed", "question.answer.completed"]},
  {key: "report", label: "Report", events: ["report.started", "report.completed", "job.completed"]},
];

const TERMINAL_STATUSES = new Set(["completed", "cancelled", "failed"]);
const BAD_STATUSES = new Set(["failed", "cancelled"]);

const $ = (id) => document.getElementById(id);

function authHeaders(extra = {}) {
  return {
    ...extra,
    ...(state.token ? {Authorization: `Bearer ${state.token}`} : {}),
  };
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("de-DE", {dateStyle: "short", timeStyle: "medium"});
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const idx = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / Math.pow(1024, idx)).toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
}

function shortId(id) {
  return String(id || "").slice(0, 8);
}

function downloadFilenameFromResponse(response) {
  const contentDisposition = response.headers.get("Content-Disposition") || "";
  const encodedMatch = contentDisposition.match(/filename\*\s*=\s*UTF-8''([^;]+)/i);
  const quotedMatch = contentDisposition.match(/filename\s*=\s*"([^"]+)"/i);
  const plainMatch = contentDisposition.match(/filename\s*=\s*([^;\s]+)/i);
  let filename = quotedMatch?.[1] || plainMatch?.[1] || "report.zip";
  if (encodedMatch?.[1]) {
    try {
      filename = decodeURIComponent(encodedMatch[1].trim().replace(/^"|"$/g, ""));
    } catch {
      // Keep the ASCII fallback when an invalid extended filename is returned.
    }
  }
  return filename.replaceAll("\\", "/").split("/").pop() || "report.zip";
}

function setBusy(isBusy) {
  ["start", "login", "authModeLogin", "authModeRegister", "refreshCapacity", "refreshJobs", "refreshJob", "retry"].forEach((id) => {
    const el = $(id);
    if (el) el.disabled = isBusy;
  });
}

function showToast(message, kind = "info") {
  const toast = $("toast");
  toast.textContent = message;
  toast.hidden = false;
  toast.style.background = kind === "error" ? "#7f1d1d" : kind === "warning" ? "#713f12" : "#0f172a";
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 4500);
}

function normalizeEvent(event) {
  return {
    id: event.id || 0,
    event_type: event.event_type || event.type || "event",
    level: event.level || "info",
    message: event.message || "",
    payload: event.payload || {},
    created_at: event.created_at || new Date().toISOString(),
  };
}

function appendEvent(event) {
  const normalized = normalizeEvent(event);
  if (normalized.id && state.eventIds.has(normalized.id)) return false;

  if (normalized.id) {
    state.eventIds.add(normalized.id);
    state.lastEventId = Math.max(state.lastEventId, normalized.id);
  }

  state.events.push(normalized);
  while (state.events.length > MAX_EVENTS) {
    const removed = state.events.shift();
    if (removed?.id) state.eventIds.delete(removed.id);
  }

  scheduleUiRender({log: true, stages: true});
  return true;
}

function addLocalLog(message, level = "info") {
  appendEvent({event_type: "frontend", level, message, created_at: new Date().toISOString()});
}

function scheduleUiRender({log = false, stages = false} = {}) {
  ui.renderLog = ui.renderLog || log;
  ui.renderStages = ui.renderStages || stages;
  if (ui.renderScheduled) return;

  ui.renderScheduled = true;
  const elapsed = Date.now() - ui.lastRenderAt;
  const delay = Math.max(0, UI_RENDER_THROTTLE_MS - elapsed);

  window.setTimeout(() => {
    window.requestAnimationFrame(() => {
      ui.renderScheduled = false;
      ui.lastRenderAt = Date.now();
      if (ui.renderLog) renderEventLog();
      if (ui.renderStages) renderStages();
      ui.renderLog = false;
      ui.renderStages = false;
    });
  }, delay);
}

function renderEventLog() {
  const filter = $("logFilter")?.value || "all";
  const log = $("eventLog");
  if (!log) return;

  const rows = state.events.map((event) => {
    const hidden = filter !== "all" && event.level !== filter ? " hidden-event" : "";
    const ts = new Date(event.created_at).toLocaleTimeString("de-DE");
    return `
      <div class="event-row${hidden}">
        <span class="event-time">${escapeHtml(ts)}</span>
        <span class="event-level">${escapeHtml(event.level)}</span>
        <span class="event-type">${escapeHtml(event.event_type)}</span>
        <span class="event-message">${escapeHtml(event.message)}</span>
      </div>
    `;
  }).join("");

  log.innerHTML = rows || `<div class="event-row"><span class="event-message">Noch keine Events.</span></div>`;
  log.scrollTop = log.scrollHeight;
}

function badgeClassForStatus(status) {
  if (["completed", "uploaded", "ok"].includes(status)) return "badge badge-success";
  if (["failed", "rejected", "error", "cancelled"].includes(status)) return "badge badge-error";
  if (["running", "queued", "cancelling"].includes(status)) return "badge badge-warning";
  return "badge badge-muted";
}


function setMainMode(mode) {
  state.mainMode = mode;
  renderShell();
}

function renderShell() {
  const loginView = $("loginView");
  const appView = $("appView");
  const createPanel = $("createPanel");
  const jobPanel = $("jobPanel");

  const isLoggedIn = Boolean(state.token);
  if (loginView) loginView.hidden = isLoggedIn;
  if (appView) appView.hidden = !isLoggedIn;

  if (!isLoggedIn) {
    closeCapacityModal();
    return;
  }

  const showMonitor = state.mainMode === "monitor" && Boolean(state.currentJobId);
  if (createPanel) createPanel.hidden = showMonitor;
  if (jobPanel) jobPanel.hidden = !showMonitor;
}

function openCapacityModal() {
  const modal = $("capacityModal");
  if (modal) modal.hidden = false;
  refreshCapacity();
}

function closeCapacityModal() {
  const modal = $("capacityModal");
  if (modal) modal.hidden = true;
}

function startNewJobView() {
  setMainMode("create");
  showToast("Neuer Auftrag vorbereitet");
}

function setSessionUi() {
  const badge = $("sessionBadge");
  const logout = $("logout");
  if (!badge || !logout) {
    renderShell();
    return;
  }
  if (state.token) {
    badge.className = "badge badge-success";
    badge.textContent = "eingeloggt";
    logout.hidden = false;
  } else {
    badge.className = "badge badge-muted";
    badge.textContent = "nicht eingeloggt";
    logout.hidden = true;
  }
  renderShell();
}

function showAuthMessage(message = "") {
  const el = $("authMessage");
  if (!el) return;
  el.textContent = message;
  el.hidden = !message;
}

function setAuthMode(mode) {
  state.authMode = mode;
  const isRegistering = mode === "register";

  $("authModeLogin")?.classList.toggle("is-active", !isRegistering);
  $("authModeRegister")?.classList.toggle("is-active", isRegistering);
  if ($("authTitle")) $("authTitle").textContent = isRegistering ? "Account erstellen" : "Analysekonsole";
  if ($("authCopy")) {
    $("authCopy").textContent = isRegistering
      ? "Account für Uploads, Jobs, WebSocket-Monitoring und Report-Download erstellen."
      : "Authentifizierung für Uploads, Jobs, WebSocket-Monitoring und Report-Download.";
  }
  if ($("password")) {
    $("password").autocomplete = isRegistering ? "new-password" : "current-password";
    $("password").value = isRegistering ? "" : $("password").value;
  }
  if ($("confirmPassword")) $("confirmPassword").value = "";
  if ($("confirmPasswordField")) $("confirmPasswordField").hidden = !isRegistering;
  if ($("login")) $("login").textContent = isRegistering ? "Account erstellen" : "Login";
  showAuthMessage("");
}

async function apiJson(url, options = {}) {
  const res = await fetch(url, {
    ...options,
    headers: authHeaders({"Content-Type": "application/json", ...(options.headers || {})}),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status} ${detail}`);
  }
  return await res.json();
}

async function login() {
  if (state.authMode === "register") {
    await registerAccount();
    return;
  }

  setBusy(true);
  showAuthMessage("");
  try {
    const data = await apiJson("/auth/login", {
      method: "POST",
      headers: {},
      body: JSON.stringify({email: $("email").value.trim(), password: $("password").value}),
    });
    state.token = data.access_token;
    sessionStorage.setItem(STORAGE_TOKEN, state.token);
    setSessionUi();
    addLocalLog("Login erfolgreich");
    await Promise.allSettled([refreshCapacity(), refreshJobs(), refreshQuestionSets()]);
    if (state.currentJobId) await selectJob(state.currentJobId, {connect: true});
    else setMainMode("create");
  } catch (err) {
    showToast("Login fehlgeschlagen", "error");
    addLocalLog(`Login fehlgeschlagen: ${err.message}`, "error");
  } finally {
    setBusy(false);
  }
}

async function registerAccount() {
  const password = $("password").value;
  if (password !== $("confirmPassword").value) {
    showAuthMessage("Passwörter stimmen nicht überein.");
    return;
  }

  setBusy(true);
  showAuthMessage("");
  try {
    const data = await apiJson("/auth/register", {
      method: "POST",
      headers: {},
      body: JSON.stringify({email: $("email").value.trim(), password}),
    });
    state.token = data.access_token;
    sessionStorage.setItem(STORAGE_TOKEN, state.token);
    setSessionUi();
    addLocalLog("Account erstellt");
    showToast("Account erstellt");
    await Promise.allSettled([refreshCapacity(), refreshJobs(), refreshQuestionSets()]);
    if (state.currentJobId) await selectJob(state.currentJobId, {connect: true});
    else setMainMode("create");
  } catch (err) {
    showToast("Registrierung fehlgeschlagen", "error");
    addLocalLog(`Registrierung fehlgeschlagen: ${err.message}`, "error");
  } finally {
    setBusy(false);
  }
}

function logout() {
  disconnectWs();
  stopPolling();
  state.token = null;
  state.currentJobId = null;
  state.currentJob = null;
  state.deadLetters = [];
  state.questionSets = [];
  state.currentQuestionSetId = null;
  state.events = [];
  state.eventIds.clear();
  state.lastEventId = 0;
  sessionStorage.removeItem(STORAGE_TOKEN);
  sessionStorage.removeItem(STORAGE_JOB);
  setSessionUi();
  renderActiveJob();
  renderEventLog();
  renderJobs();
  renderQuestionSets();
  renderCapacity(null);
  showToast("Logout abgeschlossen");
}

async function refreshCapacity() {
  if (!state.token) {
    showToast("Bitte zuerst einloggen", "warning");
    return null;
  }
  try {
    const capacity = await apiJson("/capacity");
    state.capacity = capacity;
    renderCapacity(capacity);
    return capacity;
  } catch (err) {
    renderCapacity({accepting_jobs: false, blockers: ["capacity_request_failed"], resources: {}, error: err.message});
    addLocalLog(`Kapazität konnte nicht geprüft werden: ${err.message}`, "error");
    return null;
  }
}

function renderCapacity(capacity) {
  const summary = $("capacitySummary");
  const grid = $("resourceGrid");
  if (!capacity) {
    summary.className = "capacity-summary muted";
    summary.textContent = "Noch keine Daten.";
    grid.innerHTML = "";
    return;
  }

  const blockers = capacity.blockers?.length ? capacity.blockers : [];
  summary.className = `capacity-summary ${capacity.accepting_jobs ? "" : "alert-warning"}`;
  summary.innerHTML = `
    <strong>${capacity.accepting_jobs ? "Jobs werden angenommen" : "Job-Annahme blockiert"}</strong><br>
    Blocker: ${escapeHtml(blockers.length ? blockers.join(", ") : "keine")}<br>
    Aktive Jobs: ${capacity.counts?.active_jobs ?? "?"}/${capacity.limits?.max_active_jobs ?? "?"}<br>
    Pending Worker Tasks: ${capacity.counts?.pending_worker_tasks ?? "?"}/${capacity.limits?.max_pending_worker_tasks ?? "?"}<br>
    Pending Media: ${capacity.counts?.pending_media_tasks ?? "?"}/${capacity.limits?.max_pending_media_tasks ?? "?"}
  `;

  const resources = capacity.resources || {};
  const cards = [];
  for (const [name, value] of Object.entries(resources)) {
    if (name === "vllm" && value && typeof value === "object") {
      for (const [subName, subValue] of Object.entries(value)) {
        cards.push(renderResourceCard(`vLLM ${subName}`, subValue));
      }
    } else {
      cards.push(renderResourceCard(name, value));
    }
  }
  grid.innerHTML = cards.join("") || `<div class="muted">Keine Ressourceninformationen.</div>`;
}

function renderResourceCard(name, value = {}) {
  const ok = value.ok !== false;
  const status = value.status || (ok ? "ok" : "error");
  const extra = Object.entries(value)
    .filter(([key]) => !["ok", "status"].includes(key))
    .slice(0, 3)
    .map(([key, val]) => `<div class="hint">${escapeHtml(key)}: ${escapeHtml(typeof val === "object" ? JSON.stringify(val) : val)}</div>`)
    .join("");
  return `
    <div class="resource-card">
      <strong>${escapeHtml(name)}</strong>
      <span class="${ok ? "badge badge-success" : "badge badge-error"}">${escapeHtml(status)}</span>
      ${extra}
    </div>
  `;
}

function parseQuestionsJson(showSuccess = false) {
  const status = $("questionsStatus");
  let questions;
  try {
    questions = JSON.parse($("questions").value);
  } catch (err) {
    status.textContent = `Ungültiges JSON: ${err.message}`;
    status.style.color = "#ff6b7a";
    return null;
  }

  const normalized = normalizeQuestions(questions);
  if (!normalized) return null;
  status.textContent = `${normalized.length} Frage(n) gültig.`;
  status.style.color = "#42f5a7";
  if (showSuccess) showToast("Fragenkatalog ist gültig");
  return normalized;
}

function normalizeQuestions(questions) {
  const status = $("questionsStatus");
  if (!Array.isArray(questions) || !questions.length) {
    status.textContent = "Der Fragenkatalog muss mindestens eine Frage enthalten.";
    status.style.color = "#ff6b7a";
    return null;
  }

  const normalized = [];
  for (const [idx, question] of questions.entries()) {
    const text = String(question?.text || "").trim();
    if (!text) {
      status.textContent = `Frage ${idx + 1} benötigt einen Text.`;
      status.style.color = "#ff6b7a";
      return null;
    }
    normalized.push({id: String(question?.id || `q${idx + 1}`).trim() || `q${idx + 1}`, text});
  }
  return normalized;
}

function collectQuestionsFromFields({showStatus = true} = {}) {
  const status = $("questionsStatus");
  const rows = Array.from(document.querySelectorAll(".question-row"));
  const questions = rows.map((row, idx) => ({
    id: `q${idx + 1}`,
    text: row.querySelector(".question-textarea")?.value.trim() || "",
  }));

  const normalized = normalizeQuestions(questions);
  if (!normalized) return null;

  if (showStatus) {
    status.textContent = `${normalized.length} Frage(n) gültig.`;
    status.style.color = "#42f5a7";
  }
  return normalized;
}

function syncQuestionsJsonFromFields({showStatus = false} = {}) {
  if (showStatus) {
    const questions = collectQuestionsFromFields({showStatus: true});
    if (!questions) return null;
    $("questions").value = JSON.stringify(questions, null, 2);
    return questions;
  }

  const rows = Array.from(document.querySelectorAll(".question-row"));
  const questions = rows
    .map((row, idx) => ({id: `q${idx + 1}`, text: row.querySelector(".question-textarea")?.value.trim() || ""}))
    .filter((question) => question.text);
  $("questions").value = JSON.stringify(questions.length ? questions : [{id: "q1", text: ""}], null, 2);
  return questions;
}

function validateQuestions(showSuccess = true) {
  const questions = syncQuestionsJsonFromFields({showStatus: true});
  if (questions && showSuccess) showToast("Fragenkatalog ist gültig");
  return questions;
}

function addQuestionField(text = "") {
  const box = $("questionFields");
  if (!box) return;
  const index = box.querySelectorAll(".question-row").length + 1;
  const row = document.createElement("div");
  row.className = "question-row";
  row.innerHTML = `
    <div class="question-index">Q${index}</div>
    <textarea class="question-textarea" rows="2" placeholder="Frage eingeben, z. B. Welche Narrative verbreitet der Chat?"></textarea>
    <button class="button button-icon question-remove" type="button" title="Frage entfernen">×</button>
  `;
  row.querySelector(".question-textarea").value = text;
  box.appendChild(row);
  renumberQuestionFields();
  syncQuestionsJsonFromFields({showStatus: false});
}

function setQuestionFields(questions) {
  const box = $("questionFields");
  if (!box) return;
  box.innerHTML = "";
  const normalized = normalizeQuestions(questions) || [{id: "q1", text: "Welche Narrative verbreitet der Chat?"}];
  for (const question of normalized) addQuestionField(question.text);
  renumberQuestionFields();
  syncQuestionsJsonFromFields({showStatus: false});
}

function renumberQuestionFields() {
  document.querySelectorAll(".question-row").forEach((row, idx) => {
    const index = row.querySelector(".question-index");
    if (index) index.textContent = `Q${idx + 1}`;
  });
}

function removeQuestionField(row) {
  const box = $("questionFields");
  if (!box || !row) return;
  row.remove();
  if (!box.querySelector(".question-row")) addQuestionField("");
  renumberQuestionFields();
  syncQuestionsJsonFromFields({showStatus: false});
}

function loadJsonQuestionsIntoFields() {
  const questions = parseQuestionsJson(true);
  if (!questions) return;
  setQuestionFields(questions);
  showToast("JSON wurde in Fragenfelder übernommen");
}

function validateOptions() {
  const retrievalK = Number($("retrievalK").value);
  const rerankK = Number($("rerankK").value);
  if (!Number.isInteger(retrievalK) || retrievalK < 1 || retrievalK > 200) {
    throw new Error("Retrieval-K muss zwischen 1 und 200 liegen.");
  }
  if (!Number.isInteger(rerankK) || rerankK < 1 || rerankK > 100) {
    throw new Error("Rerank-K muss zwischen 1 und 100 liegen.");
  }
  if (rerankK > retrievalK) {
    throw new Error("Rerank-K darf nicht größer als Retrieval-K sein.");
  }
  return {retrievalK, rerankK};
}

function updateFileInfo() {
  const file = $("file").files[0];
  const info = $("fileInfo");
  if (!file) {
    info.textContent = "Keine Datei ausgewählt.";
    return;
  }
  const isZip = file.name.toLowerCase().endsWith(".zip");
  info.textContent = `${file.name} · ${formatBytes(file.size)}${isZip ? "" : " · Warnung: Datei endet nicht auf .zip"}`;
  info.style.color = isZip ? "#83a6b8" : "#ff6b7a";
}

async function uploadFileViaBackend(upload, file) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    $("uploadProgressWrap").hidden = false;
    setUploadProgress(0, {force: true});

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        setUploadProgress((event.loaded / event.total) * 100);
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        setUploadProgress(100, {force: true});
        resolve(JSON.parse(xhr.responseText || "{}"));
      } else {
        reject(new Error(`${xhr.status} ${xhr.responseText}`));
      }
    };
    xhr.onerror = () => reject(new Error("Netzwerkfehler beim Upload"));
    xhr.onabort = () => reject(new Error("Upload wurde abgebrochen"));
    xhr.open("PUT", upload.backend_upload_url);
    xhr.setRequestHeader("Authorization", `Bearer ${state.token}`);
    xhr.setRequestHeader("Content-Type", "application/zip");
    xhr.send(file);
  });
}

async function uploadFileForAnalysis(upload, file) {
  return await uploadFileViaBackend(upload, file);
}

function setUploadProgress(percent, {force = false} = {}) {
  const value = Math.max(0, Math.min(100, Number(percent || 0)));
  const now = Date.now();
  if (!force && value < 100 && now - ui.lastUploadProgressAt < UPLOAD_PROGRESS_THROTTLE_MS) return;
  ui.lastUploadProgressAt = now;

  const bar = $("uploadProgressBar");
  const text = $("uploadProgressText");
  if (bar) bar.style.width = `${value}%`;
  if (text) text.textContent = `${Math.round(value)}%`;
}

async function startJob() {
  if (!state.token) {
    showToast("Bitte zuerst einloggen", "warning");
    return;
  }

  const questions = validateQuestions(false);
  if (!questions) return;

  let options;
  try {
    options = getCurrentOptionsPayload();
  } catch (err) {
    showToast(err.message, "error");
    return;
  }

  const file = $("file").files[0];
  if (!file) {
    showToast("Bitte ZIP auswählen", "warning");
    return;
  }
  if (!file.name.toLowerCase().endsWith(".zip")) {
    showToast("Nur .zip-Dateien werden angenommen", "error");
    return;
  }

  setBusy(true);
  state.uploadInProgress = true;
  try {
    const capacity = await refreshCapacity();
    if (capacity && !capacity.accepting_jobs) {
      throw new Error(`System nimmt aktuell keine neuen Jobs an: ${(capacity.blockers || []).join(", ")}`);
    }

    const upload = await apiJson("/uploads", {
      method: "POST",
      body: JSON.stringify({filename: file.name, size_bytes: file.size}),
    });
    addLocalLog("Upload-Ziel erhalten");

    await uploadFileForAnalysis(upload, file);
    addLocalLog("Upload abgeschlossen, starte Job");

    const jobPayload = {upload_id: upload.upload_id, questions, options};
    if (state.currentQuestionSetId) jobPayload.question_set_id = state.currentQuestionSetId;

    const job = await apiJson("/jobs", {
      method: "POST",
      body: JSON.stringify(jobPayload),
    });

    await selectJob(job.id, {connect: true});
    addLocalLog(`Job gestartet: ${job.id}`);
    showToast("Job gestartet");
    await Promise.allSettled([refreshJobs(), refreshCapacity()]);
  } catch (err) {
    showToast(err.message, "error");
    addLocalLog(`Job-Start fehlgeschlagen: ${err.message}`, "error");
  } finally {
    state.uploadInProgress = false;
    setBusy(false);
  }
}

function getSelectedQuestionSet() {
  if (!state.currentQuestionSetId) return null;
  return state.questionSets.find((item) => item.id === state.currentQuestionSetId) || null;
}

function getCurrentOptionsPayload() {
  const {retrievalK, rerankK} = validateOptions();
  return {
    translate: $("translate").checked,
    analyze_media: $("analyzeMedia").checked,
    retrieval_k: retrievalK,
    rerank_k: rerankK,
  };
}

function applyOptions(options = {}) {
  $("translate").checked = Boolean(options.translate);
  $("analyzeMedia").checked = options.analyze_media !== false;
  $("retrievalK").value = Number(options.retrieval_k || 50);
  $("rerankK").value = Number(options.rerank_k || 15);
}

function questionSetPayloadFromForm(name, description = null) {
  const questions = validateQuestions(false);
  if (!questions) return null;
  return {
    name,
    description,
    questions,
    default_options: getCurrentOptionsPayload(),
  };
}

async function refreshQuestionSets() {
  if (!state.token) return;
  try {
    state.questionSets = await apiJson("/question-sets");
    renderQuestionSets();
  } catch (err) {
    addLocalLog(`Fragensets konnten nicht geladen werden: ${err.message}`, "warning");
  }
}

function renderQuestionSets() {
  const select = $("questionSetSelect");
  const status = $("questionSetStatus");
  if (!select) return;

  const previous = state.currentQuestionSetId || select.value || "";
  const options = [`<option value="">Kein Set ausgewählt</option>`].concat(
    state.questionSets.map((item) => {
      const selected = item.id === previous ? " selected" : "";
      return `<option value="${escapeHtml(item.id)}"${selected}>${escapeHtml(item.name)} (${item.question_count || item.questions?.length || 0})</option>`;
    })
  );
  select.innerHTML = options.join("");

  if (previous && state.questionSets.some((item) => item.id === previous)) {
    select.value = previous;
    state.currentQuestionSetId = previous;
    const selected = getSelectedQuestionSet();
    if (status && selected) {
      status.textContent = `Geladenes Set: ${selected.name}. Änderungen betreffen den Job erst nach „Aktualisieren“ dauerhaft.`;
      status.style.color = "#42f5a7";
    }
  } else {
    select.value = "";
    state.currentQuestionSetId = null;
    if (status) {
      status.textContent = "Keine gespeicherte Vorlage ausgewählt.";
      status.style.color = "#83a6b8";
    }
  }
}

function applyQuestionSet(questionSet) {
  if (!questionSet) return;
  state.currentQuestionSetId = questionSet.id;
  const select = $("questionSetSelect");
  if (select) select.value = questionSet.id;
  setQuestionFields(questionSet.questions || []);
  applyOptions(questionSet.default_options || {});
  validateQuestions(false);
  renderQuestionSets();
  showToast(`Fragenset geladen: ${questionSet.name}`);
}

async function loadSelectedQuestionSet() {
  const select = $("questionSetSelect");
  const id = select?.value || "";
  if (!id) {
    state.currentQuestionSetId = null;
    renderQuestionSets();
    return;
  }
  try {
    const questionSet = await apiJson(`/question-sets/${id}`);
    const idx = state.questionSets.findIndex((item) => item.id === questionSet.id);
    if (idx >= 0) state.questionSets[idx] = questionSet;
    else state.questionSets.push(questionSet);
    applyQuestionSet(questionSet);
  } catch (err) {
    showToast(`Fragenset konnte nicht geladen werden: ${err.message}`, "error");
  }
}

async function saveCurrentQuestionSet() {
  try {
    const defaultName = getSelectedQuestionSet()?.name || "Neues Fragenset";
    const name = window.prompt("Name des Fragensets", defaultName);
    if (!name) return;
    const description = window.prompt("Beschreibung (optional)", "") || null;
    const payload = questionSetPayloadFromForm(name, description);
    if (!payload) return;
    const created = await apiJson("/question-sets", {method: "POST", body: JSON.stringify(payload)});
    state.currentQuestionSetId = created.id;
    await refreshQuestionSets();
    showToast(`Fragenset gespeichert: ${created.name}`);
  } catch (err) {
    showToast(`Fragenset konnte nicht gespeichert werden: ${err.message}`, "error");
  }
}

async function updateCurrentQuestionSet() {
  const selected = getSelectedQuestionSet();
  if (!selected) {
    showToast("Bitte zuerst ein Fragenset auswählen", "warning");
    return;
  }
  try {
    const name = window.prompt("Name des Fragensets", selected.name);
    if (!name) return;
    const description = window.prompt("Beschreibung (optional)", selected.description || "") || null;
    const payload = questionSetPayloadFromForm(name, description);
    if (!payload) return;
    const updated = await apiJson(`/question-sets/${selected.id}`, {method: "PATCH", body: JSON.stringify(payload)});
    state.currentQuestionSetId = updated.id;
    await refreshQuestionSets();
    showToast(`Fragenset aktualisiert: ${updated.name}`);
  } catch (err) {
    showToast(`Fragenset konnte nicht aktualisiert werden: ${err.message}`, "error");
  }
}

async function duplicateCurrentQuestionSet() {
  const selected = getSelectedQuestionSet();
  if (!selected) {
    showToast("Bitte zuerst ein Fragenset auswählen", "warning");
    return;
  }
  try {
    const duplicated = await apiJson(`/question-sets/${selected.id}/duplicate`, {method: "POST"});
    state.currentQuestionSetId = duplicated.id;
    await refreshQuestionSets();
    applyQuestionSet(duplicated);
    showToast(`Fragenset dupliziert: ${duplicated.name}`);
  } catch (err) {
    showToast(`Fragenset konnte nicht dupliziert werden: ${err.message}`, "error");
  }
}

async function deleteCurrentQuestionSet() {
  const selected = getSelectedQuestionSet();
  if (!selected) {
    showToast("Bitte zuerst ein Fragenset auswählen", "warning");
    return;
  }
  if (!window.confirm(`Fragenset „${selected.name}“ archivieren?`)) return;
  try {
    await apiJson(`/question-sets/${selected.id}`, {method: "DELETE"});
    state.currentQuestionSetId = null;
    await refreshQuestionSets();
    showToast("Fragenset archiviert");
  } catch (err) {
    showToast(`Fragenset konnte nicht archiviert werden: ${err.message}`, "error");
  }
}

async function refreshJobs() {
  if (!state.token) return;
  try {
    state.jobs = await apiJson("/jobs");
    renderJobs();
  } catch (err) {
    addLocalLog(`Jobliste konnte nicht geladen werden: ${err.message}`, "error");
  }
}

function renderJobs() {
  const box = $("jobsList");
  if (!state.token) {
    box.className = "jobs-list empty-state";
    box.textContent = "Bitte einloggen.";
    return;
  }
  if (!state.jobs.length) {
    box.className = "jobs-list empty-state";
    box.textContent = "Keine Jobs vorhanden.";
    return;
  }
  box.className = "jobs-list";
  box.innerHTML = state.jobs.map((job) => {
    const selected = job.id === state.currentJobId ? " is-selected" : "";
    return `
      <div class="job-item${selected}">
        <div>
          <code title="${escapeHtml(job.id)}">${escapeHtml(shortId(job.id))}</code>
          <div class="hint">${escapeHtml(formatDate(job.created_at))}</div>
        </div>
        <span class="${badgeClassForStatus(job.status)}">${escapeHtml(job.status)}</span>
        <button class="button button-secondary button-small" type="button" data-select-job="${escapeHtml(job.id)}">Öffnen</button>
      </div>
    `;
  }).join("");
}

async function selectJob(jobId, {connect = true} = {}) {
  state.currentJobId = jobId;
  state.mainMode = "monitor";
  sessionStorage.setItem(STORAGE_JOB, jobId);
  state.events = [];
  state.eventIds.clear();
  state.lastEventId = 0;
  await refreshJobStatus();
  await loadEventBacklog();
  renderActiveJob();
  if (connect) {
    stopPolling();
    connectWs();
  } else {
    startPolling();
  }
}

async function refreshJobStatus() {
  if (!state.token || !state.currentJobId) return;
  try {
    const [job, deadLetters] = await Promise.all([
      apiJson(`/jobs/${state.currentJobId}`),
      apiJson(`/jobs/${state.currentJobId}/dead-letters`).catch(() => []),
    ]);
    state.currentJob = job;
    state.deadLetters = deadLetters || [];
    renderActiveJob();
    return job;
  } catch (err) {
    addLocalLog(`Job-Status konnte nicht geladen werden: ${err.message}`, "error");
  }
}

async function loadEventBacklog() {
  if (!state.token || !state.currentJobId) return;
  try {
    const events = await apiJson(`/jobs/${state.currentJobId}/events?after_id=${state.lastEventId}`);
    for (const event of events) appendEvent(event);
  } catch (err) {
    addLocalLog(`Events konnten nicht geladen werden: ${err.message}`, "warning");
  }
}

function renderActiveJob() {
  renderShell();
  const empty = $("activeJobEmpty");
  const panel = $("activeJob");
  const job = state.currentJob;

  if (!state.currentJobId || !job) {
    empty.hidden = false;
    panel.hidden = true;
    $("cancel").hidden = true;
    $("retry").hidden = true;
    $("download").hidden = true;
    renderJobDashboard([]);
    return;
  }

  empty.hidden = true;
  panel.hidden = false;
  $("activeJobId").textContent = job.id;
  $("activeJobStatus").className = badgeClassForStatus(job.status);
  $("activeJobStatus").textContent = job.status;
  $("activeJobCreated").textContent = formatDate(job.created_at);
  $("cancel").hidden = TERMINAL_STATUSES.has(job.status);
  $("retry").hidden = job.status !== "failed";
  $("download").hidden = job.status !== "completed";
  $("download").disabled = Boolean(state.downloadInProgress);
  $("download").textContent = state.downloadInProgress
    ? "Download wird vorbereitet…"
    : job.source_type === "upload"
      ? "Alles herunterladen"
      : "Report herunterladen";

  const error = $("jobError");
  if (job.error_message) {
    error.hidden = false;
    error.textContent = job.error_message;
  } else {
    error.hidden = true;
  }

  const dead = $("deadLetterBox");
  if (state.deadLetters.length) {
    const latest = state.deadLetters[0];
    dead.hidden = false;
    dead.innerHTML = `
      <strong>${state.deadLetters.length} Dead Letter</strong><br>
      Letzter Fehler: ${escapeHtml(latest.subject)} · ${escapeHtml(latest.reason)}<br>
      ${escapeHtml(latest.error_message || "")}
    `;
  } else {
    dead.hidden = true;
  }

  renderStages();
  renderJobs();
}

function getStageState() {
  const latestRetryIndex = state.events.findLastIndex((event) => event.event_type === "job.retry.started");
  return STAGES.map((stage) => {
    const matching = state.events
      .map((event, index) => ({event, index}))
      .filter((item) => stage.events.includes(item.event.event_type));
    const matchingAfterRetry = latestRetryIndex >= 0
      ? matching.filter((item) => item.index > latestRetryIndex)
      : matching;
    const effectiveFailures = latestRetryIndex >= 0 ? matchingAfterRetry : matching;
    const latest = (matchingAfterRetry.at(-1) || matching.at(-1))?.event;
    const completed = matching.some((item) => /completed$/.test(item.event.event_type) || item.event.event_type === "job.completed");
    const started = Boolean(matchingAfterRetry.length || completed || matching.length);
    const failed = effectiveFailures.some((item) => item.event.level === "error" || item.event.event_type.includes("failed"));
    let status = "pending";
    if (failed) status = "failed";
    else if (completed) status = "completed";
    else if (started) status = "running";
    return {stage, latest, started, completed, failed, status};
  });
}

function renderStages() {
  const list = $("stageList");
  if (!list) return;

  const stageStates = getStageState();
  renderJobDashboard(stageStates);

  list.innerHTML = stageStates.map((item) => {
    const {stage, status} = item;
    const payloadText = formatProgressPayload(item.latest?.payload);
    const message = item.latest?.message || (status === "pending" ? "wartet" : status);
    const cls = status === "completed" ? "badge-success" : status === "failed" ? "badge-error" : status === "running" ? "badge-warning" : "badge-muted";

    return `
      <div class="stage stage-${escapeHtml(status)}">
        <div class="stage-name">${escapeHtml(stage.label)}</div>
        <div class="stage-message" title="${escapeHtml(message)}">${escapeHtml(message)}${payloadText ? ` · ${escapeHtml(payloadText)}` : ""}</div>
        <span class="badge ${cls}">${escapeHtml(status)}</span>
      </div>
    `;
  }).join("");
}

function renderJobDashboard(stageStates = getStageState()) {
  const progressLabel = $("pipelineProgressLabel");
  if (!progressLabel) return;

  const completed = stageStates.filter((item) => item.status === "completed").length;
  const running = stageStates.find((item) => item.status === "running");
  const failed = stageStates.find((item) => item.status === "failed");
  const current = failed || running || [...stageStates].reverse().find((item) => item.status === "completed") || stageStates[0];
  const percent = stageStates.length ? Math.round((completed / stageStates.length) * 100) : 0;

  progressLabel.textContent = `${completed}/${stageStates.length}`;
  $("pipelineProgressBar").style.width = `${percent}%`;
  $("currentStageLabel").textContent = current?.stage?.label || "-";
  $("eventCountLabel").textContent = String(state.events.length);
  $("deadLetterCountLabel").textContent = String(state.deadLetters.length);
}

function formatProgressPayload(payload = {}) {
  if (!payload || typeof payload !== "object") return "";
  const done = payload.done
    ?? payload.completed
    ?? payload.media_done
    ?? payload.questions_done
    ?? payload.chunks_done
    ?? payload.texts_done;
  const total = payload.total
    ?? payload.media_total
    ?? payload.questions_total
    ?? payload.chunks_total
    ?? payload.texts_total;
  if (done !== undefined && total !== undefined) return `${done}/${total}`;
  if (payload.progress !== undefined) return `${payload.progress}%`;
  return "";
}

async function connectWs() {
  disconnectWs();
  if (!state.token || !state.currentJobId) return;

  const jobId = state.currentJobId;
  setWsStatus("verbinde", "badge-warning");
  let ticket;
  try {
    ({ticket} = await apiJson(`/jobs/${jobId}/ws-ticket`, {method: "POST"}));
  } catch (err) {
    setWsStatus("fehler", "badge-error");
    addLocalLog(`WebSocket-Ticket fehlgeschlagen: ${err.message}`, "warning");
    startPolling();
    window.clearTimeout(state.wsReconnectTimer);
    state.wsReconnectTimer = window.setTimeout(() => connectWs(), 3000);
    return;
  }

  if (!state.token || state.currentJobId !== jobId) return;
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${scheme}://${location.host}/ws/jobs/${jobId}?ticket=${encodeURIComponent(ticket)}`);
  state.ws = ws;

  ws.onopen = () => {
    stopPolling();
    setWsStatus("verbunden", "badge-success");
    addLocalLog("WebSocket verbunden");
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      appendEvent(data);
      handleJobEvent(data);
    } catch (err) {
      addLocalLog(`Ungültige WebSocket-Nachricht: ${err.message}`, "warning");
    }
  };

  ws.onerror = () => {
    setWsStatus("fehler", "badge-error");
  };

  ws.onclose = () => {
    setWsStatus("getrennt", "badge-muted");
    if (state.currentJob && !TERMINAL_STATUSES.has(state.currentJob.status)) {
      startPolling();
      window.clearTimeout(state.wsReconnectTimer);
      state.wsReconnectTimer = window.setTimeout(() => connectWs(), 3000);
    } else {
      stopPolling();
    }
  };
}

function disconnectWs() {
  window.clearTimeout(state.wsReconnectTimer);
  if (state.ws) {
    state.ws.onclose = null;
    state.ws.close();
    state.ws = null;
  }
  setWsStatus("getrennt", "badge-muted");
}

function setWsStatus(text, badgeClass) {
  const el = $("wsStatus");
  if (!el) return;
  el.className = `badge ${badgeClass}`;
  el.textContent = text;
}

function scheduleJobStatusRefresh(delay = 900) {
  window.clearTimeout(ui.jobStatusRefreshTimer);
  ui.jobStatusRefreshTimer = window.setTimeout(() => refreshJobStatus(), delay);
}

function scheduleJobsRefresh(delay = 1500) {
  window.clearTimeout(ui.jobsRefreshTimer);
  ui.jobsRefreshTimer = window.setTimeout(() => refreshJobs(), delay);
}

function scheduleCapacityRefresh(delay = 2000) {
  window.clearTimeout(ui.capacityRefreshTimer);
  ui.capacityRefreshTimer = window.setTimeout(() => refreshCapacity(), delay);
}

function handleJobEvent(event) {
  if (["job.completed", "job.failed", "job.cancelled", "job.retry.started"].includes(event.event_type)) {
    scheduleJobStatusRefresh(100);
    scheduleJobsRefresh(250);
    scheduleCapacityRefresh(500);
    if (event.event_type === "job.completed") showToast("Report ist fertig");
    return;
  }

  if (["worker.task.dead_letter", "worker.task.retrying", "media.analysis.failed"].includes(event.event_type)) {
    scheduleJobStatusRefresh();
  }
}

function startPolling() {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) return;
  stopPolling();
  state.pollTimer = window.setInterval(async () => {
    if (!state.currentJobId || !state.token) return;
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      stopPolling();
      return;
    }
    await Promise.allSettled([refreshJobStatus(), loadEventBacklog()]);
    if (state.currentJob && TERMINAL_STATUSES.has(state.currentJob.status)) {
      stopPolling();
    }
  }, 5000);
}

function stopPolling() {
  window.clearInterval(state.pollTimer);
  state.pollTimer = null;
}

async function cancelJob() {
  if (!state.token || !state.currentJobId) return;
  if (!window.confirm("Job wirklich abbrechen?")) return;
  try {
    const data = await apiJson(`/jobs/${state.currentJobId}/cancel`, {method: "POST"});
    addLocalLog(`Abbruch angefordert: ${data.status}`, "warning");
    await refreshJobStatus();
  } catch (err) {
    showToast(`Abbruch fehlgeschlagen: ${err.message}`, "error");
    addLocalLog(`Abbruch fehlgeschlagen: ${err.message}`, "error");
  }
}

async function retryJob() {
  if (!state.token || !state.currentJobId) return;
  if (!window.confirm("Diese fehlgeschlagene Analyse ab dem letzten fehlgeschlagenen Schritt erneut versuchen?")) return;
  try {
    const job = await apiJson(`/jobs/${state.currentJobId}/retry`, {method: "POST"});
    state.currentJob = job;
    addLocalLog(`Retry angefordert: ${job.status}`, "warning");
    showToast("Retry gestartet");
    await Promise.allSettled([refreshJobStatus(), loadEventBacklog(), refreshJobs(), refreshCapacity()]);
    connectWs();
  } catch (err) {
    showToast(`Retry fehlgeschlagen: ${err.message}`, "error");
    addLocalLog(`Retry fehlgeschlagen: ${err.message}`, "error");
    await refreshJobStatus();
  }
}

async function downloadResult() {
  if (!state.token || !state.currentJobId) return;
  const includeOriginal = state.currentJob?.source_type === "upload";
  const downloadPath = includeOriginal ? "download-all" : "download";
  const successMessage = includeOriginal ? "Gesamt-Download gestartet" : "Report-Download gestartet";
  const failureMessage = includeOriginal ? "Gesamt-Download fehlgeschlagen" : "Report-Download fehlgeschlagen";
  state.downloadInProgress = true;
  renderActiveJob();
  try {
    const token = state.token;
    const currentJobId = state.currentJobId;
    const res = await fetch(`/jobs/${currentJobId}/report/${downloadPath}`, {headers: {"Authorization": `Bearer ${token}`}});
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = downloadFilenameFromResponse(res);
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    addLocalLog(successMessage);
  } catch (err) {
    showToast(`${failureMessage}: ${err.message}`, "error");
    addLocalLog(`${failureMessage}: ${err.message}`, "error");
  } finally {
    state.downloadInProgress = false;
    renderActiveJob();
  }
}

function resetForm() {
  $("file").value = "";
  updateFileInfo();
  setQuestionFields([
    {id: "q1", text: "Welche Narrative verbreitet der Chat?"},
  ]);
  $("translate").checked = false;
  $("analyzeMedia").checked = true;
  $("retrievalK").value = 50;
  $("rerankK").value = 15;
  $("questionsStatus").textContent = "Noch nicht validiert.";
  $("questionsStatus").style.color = "#83a6b8";
  $("uploadProgressWrap").hidden = true;
  state.currentQuestionSetId = null;
  renderQuestionSets();
  setUploadProgress(0, {force: true});
}

function bindUploadDropZone() {
  const zone = $("uploadZone");
  const fileInput = $("file");
  if (!zone || !fileInput) return;

  ["dragenter", "dragover"].forEach((name) => {
    zone.addEventListener(name, (event) => {
      event.preventDefault();
      event.stopPropagation();
      zone.classList.add("is-dragging");
    });
  });

  ["dragleave", "drop"].forEach((name) => {
    zone.addEventListener(name, (event) => {
      event.preventDefault();
      event.stopPropagation();
      zone.classList.remove("is-dragging");
    });
  });

  zone.addEventListener("drop", (event) => {
    const files = event.dataTransfer?.files;
    if (!files || !files.length) return;
    fileInput.files = files;
    updateFileInfo();
  });
}

function bindEvents() {
  $("login").addEventListener("click", login);
  $("authModeLogin").addEventListener("click", () => setAuthMode("login"));
  $("authModeRegister").addEventListener("click", () => setAuthMode("register"));
  $("logout").addEventListener("click", logout);
  $("newJob").addEventListener("click", startNewJobView);
  $("openCapacity").addEventListener("click", openCapacityModal);
  $("closeCapacity").addEventListener("click", closeCapacityModal);
  $("capacityModal").addEventListener("click", (event) => {
    if (event.target.id === "capacityModal") closeCapacityModal();
  });
  $("refreshCapacity").addEventListener("click", refreshCapacity);
  $("start").addEventListener("click", startJob);
  $("cancel").addEventListener("click", cancelJob);
  $("retry").addEventListener("click", retryJob);
  $("download").addEventListener("click", downloadResult);
  $("refreshJobs").addEventListener("click", refreshJobs);
  $("refreshJob").addEventListener("click", () => Promise.allSettled([refreshJobStatus(), loadEventBacklog()]));
  $("clearLog").addEventListener("click", () => {
    state.events = [];
    state.eventIds.clear();
    state.lastEventId = 0;
    renderEventLog();
  });
  $("logFilter").addEventListener("change", renderEventLog);
  $("file").addEventListener("change", updateFileInfo);
  bindUploadDropZone();
  $("refreshQuestionSets").addEventListener("click", refreshQuestionSets);
  $("questionSetSelect").addEventListener("change", (event) => {
    state.currentQuestionSetId = event.target.value || null;
    renderQuestionSets();
  });
  $("loadQuestionSet").addEventListener("click", loadSelectedQuestionSet);
  $("saveQuestionSet").addEventListener("click", saveCurrentQuestionSet);
  $("updateQuestionSet").addEventListener("click", updateCurrentQuestionSet);
  $("duplicateQuestionSet").addEventListener("click", duplicateCurrentQuestionSet);
  $("deleteQuestionSet").addEventListener("click", deleteCurrentQuestionSet);
  $("addQuestion").addEventListener("click", () => addQuestionField(""));
  $("validateQuestions").addEventListener("click", () => parseQuestionsJson(true));
  $("formatQuestions").addEventListener("click", () => {
    const questions = parseQuestionsJson(false);
    if (questions) $("questions").value = JSON.stringify(questions, null, 2);
  });
  $("loadJsonToFields").addEventListener("click", loadJsonQuestionsIntoFields);
  $("exampleQuestions").addEventListener("click", () => {
    setQuestionFields([
      {id: "q1", text: "Welche Narrative verbreitet der Chat?"},
      {id: "q2", text: "Welche Akteure werden positiv oder negativ dargestellt?"},
      {id: "q3", text: "Welche wiederkehrenden Mobilisierungsaufrufe erscheinen im Chat?"},
    ]);
    validateQuestions(false);
  });
  $("questionFields").addEventListener("input", (event) => {
    if (event.target.closest(".question-textarea")) syncQuestionsJsonFromFields({showStatus: false});
  });
  $("questionFields").addEventListener("click", (event) => {
    const button = event.target.closest(".question-remove");
    if (button) removeQuestionField(button.closest(".question-row"));
  });
  $("clearForm").addEventListener("click", resetForm);
  $("copyJobId").addEventListener("click", async () => {
    if (!state.currentJobId) return;
    await navigator.clipboard.writeText(state.currentJobId).catch(() => null);
    showToast("Job-ID kopiert");
  });

  $("jobsList").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-select-job]");
    if (!button) return;
    await selectJob(button.dataset.selectJob, {connect: true});
  });

  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      startJob();
    }
  });
}

async function init() {
  bindEvents();
  setQuestionFields([
    {id: "q1", text: "Welche Narrative verbreitet der Chat?"},
  ]);
  setSessionUi();
  updateFileInfo();
  renderEventLog();
  renderActiveJob();
  renderCapacity(null);
  renderShell();

  if (state.token) {
    await Promise.allSettled([refreshCapacity(), refreshJobs(), refreshQuestionSets()]);
    if (state.currentJobId) await selectJob(state.currentJobId, {connect: true});
    else setMainMode("create");
  }
}

init();
