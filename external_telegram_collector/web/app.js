const byId = (id) => document.getElementById(id);

const phaseNames = {
  starting: "Starting",
  connecting: "Connecting",
  awaiting_code: "Code required",
  awaiting_password: "Password required",
  authorized: "Authorized",
  registering: "Registering chats",
  idle: "Connected",
  syncing: "Synchronizing",
  retrying: "Retrying",
  configuration_error: "Configuration error",
};

const connectedPhases = new Set(["authorized", "registering", "idle", "syncing"]);
const failedPhases = new Set(["configuration_error", "retrying"]);
let requestInFlight = false;

function show(element, visible) {
  element.classList.toggle("is-hidden", !visible);
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(date);
}

function formatRange(start, end) {
  return `${formatDate(start)} – ${formatDate(end)}`;
}

function setRequestError(message) {
  const notice = byId("request-error");
  notice.textContent = message || "";
  show(notice, Boolean(message));
}

function renderLogin(status) {
  const needsCode = status.phase === "awaiting_code";
  const needsPassword = status.phase === "awaiting_password";
  show(byId("login-card"), needsCode || needsPassword);
  show(byId("code-form"), needsCode);
  show(byId("password-form"), needsPassword);

  if (needsCode) {
    byId("login-title").textContent = "Enter verification code";
    byId("login-copy").textContent = status.message;
  } else if (needsPassword) {
    byId("login-title").textContent = "Enter Telegram password";
    byId("login-copy").textContent = status.message;
  }
}

function renderRegistration(registration) {
  if (!registration) {
    byId("registration-title").textContent = "Waiting to register";
    byId("registration-supported").textContent = "—";
    byId("registration-matched").textContent = "—";
    byId("registration-count").textContent = "—";
    return;
  }
  byId("registration-title").textContent = `${registration.registered} chat${registration.registered === 1 ? "" : "s"} ready`;
  byId("registration-supported").textContent = registration.supported;
  byId("registration-matched").textContent = registration.matched;
  byId("registration-count").textContent = registration.registered;
}

function renderCurrentRun(run) {
  const card = byId("current-run-card");
  show(card, Boolean(run));
  if (!run) return;
  byId("current-chat").textContent = run.chat_title;
  byId("current-range").textContent = formatRange(run.requested_start, run.requested_end);
  byId("current-messages").textContent = run.messages_seen;
  byId("current-attachments").textContent = run.attachments_seen;
  byId("current-failures").textContent = run.attachments_failed;
}

function renderLastRun(run) {
  const card = byId("last-run-card");
  show(card, Boolean(run));
  if (!run) return;
  byId("last-chat").textContent = run.chat_title;
  byId("last-completed").textContent = `Finished ${formatDate(run.completed_at)}`;
  byId("last-messages").textContent = run.messages_seen;
  byId("last-attachments").textContent = run.attachments_seen;
  byId("last-failures").textContent = run.attachments_failed;

  const result = byId("last-result");
  result.textContent = run.status === "completed" ? "Completed" : "Failed";
  result.className = `result-pill ${run.status}`;
  const error = byId("last-error");
  error.textContent = run.error || "";
  show(error, Boolean(run.error));
}

function renderEvents(events) {
  const list = byId("event-list");
  byId("event-count").textContent = `${events.length} event${events.length === 1 ? "" : "s"}`;
  if (!events.length) return;

  const fragment = document.createDocumentFragment();
  [...events].reverse().forEach((event) => {
    const item = document.createElement("li");
    const time = document.createElement("span");
    const level = document.createElement("span");
    const message = document.createElement("span");
    time.className = "event-time";
    time.textContent = new Date(event.timestamp).toLocaleTimeString();
    level.className = `event-level ${event.level}`;
    level.textContent = event.level;
    message.textContent = event.message;
    item.append(time, level, message);
    fragment.append(item);
  });
  list.replaceChildren(fragment);
}

function render(status) {
  byId("phase-label").textContent = phaseNames[status.phase] || status.phase;
  byId("status-message").textContent = status.message;
  byId("updated-at").textContent = formatDate(status.updated_at);
  byId("account-name").textContent = status.account
    ? `${status.account.display_name}${status.account.username ? ` (@${status.account.username})` : ""}`
    : "Not connected";

  const dot = byId("phase-dot");
  dot.className = "phase-dot";
  if (connectedPhases.has(status.phase)) dot.classList.add("connected");
  if (failedPhases.has(status.phase)) dot.classList.add("failed");

  const retryCopy = byId("retry-copy");
  retryCopy.textContent = status.retry
    ? `Retry ${status.retry.attempt} is scheduled for ${formatDate(status.retry.retry_at)}.`
    : "";
  show(retryCopy, Boolean(status.retry));

  renderLogin(status);
  renderRegistration(status.registration);
  renderCurrentRun(status.current_run);
  renderLastRun(status.last_run);
  renderEvents(status.events || []);
}

async function fetchStatus() {
  if (requestInFlight) return;
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error(`Status request failed (${response.status})`);
    render(await response.json());
  } catch (error) {
    setRequestError(error.message || "Collector status is unavailable");
  }
}

async function post(path, payload = {}) {
  if (requestInFlight) return;
  requestInFlight = true;
  document.querySelectorAll("button").forEach((button) => { button.disabled = true; });
  setRequestError("");
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    render(data);
  } catch (error) {
    setRequestError(error.message || "The collector rejected the request");
    await fetchStatus();
  } finally {
    requestInFlight = false;
    document.querySelectorAll("button").forEach((button) => { button.disabled = false; });
  }
}

byId("code-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = byId("code-input");
  const code = input.value.trim();
  if (!code) return;
  input.value = "";
  await post("/api/login/code", { code });
});

byId("password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = byId("password-input");
  const password = input.value;
  if (!password) return;
  input.value = "";
  await post("/api/login/password", { password });
});

byId("resend-button").addEventListener("click", () => post("/api/login/resend"));

fetchStatus();
window.setInterval(fetchStatus, 1500);
