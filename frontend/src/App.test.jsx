import React from "react";
import { act, create } from "react-test-renderer";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { apiJson, uploadFileForAnalysis } from "./api/client";
import { DEFAULT_OPTIONS, DEFAULT_QUESTIONS, STORAGE_JOB, STORAGE_TOKEN } from "./lib/constants";

vi.mock("./api/client", () => ({
  apiJson: vi.fn(), downloadBlob: vi.fn(), uploadFileForAnalysis: vi.fn(),
  buildWsUrl: (path) => `ws://test${path}`,
}));
vi.mock("./components/AppSidebar", () => ({ AppSidebar: (props) => <sidebar {...props} /> }));
vi.mock("./components/CreateJobPanel", () => ({ CreateJobPanel: (props) => <create-panel {...props} /> }));
vi.mock("./components/JobMonitorPanel", () => ({ JobMonitorPanel: (props) => <job-monitor {...props} /> }));
vi.mock("./components/LoginView", () => ({ LoginView: (props) => <login-view {...props} /> }));
vi.mock("./components/TelegramSourcesPanel", () => ({ TelegramSourcesPanel: () => null }));
vi.mock("./components/Toast", () => ({ Toast: () => null }));
vi.mock("./components/TutorialPage", () => ({ TutorialPage: () => null }));

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

let renderer;
let intercept;
let sockets;
const input = {
  sourceMode: "telegram_chat", telegramChatId: "chat",
  reportStart: "2026-09-01T00:00", reportEnd: "2026-09-02T00:00",
};
const props = (type) => renderer.root.findByType(type).props;
async function mount(job) {
  if (job) sessionStorage.setItem(STORAGE_JOB, job);
  await act(async () => { renderer = create(<App />); });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  intercept = () => undefined;
  sockets = [];
  const stored = new Map([[STORAGE_TOKEN, "token-1"]]);
  vi.stubGlobal("sessionStorage", {
    getItem: (key) => stored.get(key) ?? null,
    setItem: (key, value) => stored.set(key, value),
    removeItem: (key) => stored.delete(key),
  });
  vi.stubGlobal("window", {
    setTimeout, clearTimeout, setInterval, clearInterval, confirm: () => true,
  });
  vi.stubGlobal("WebSocket", class {
    constructor(url) { this.url = url; sockets.push(this); }
    close() {}
  });
  apiJson.mockImplementation(async (path, config) => {
    const result = intercept(path, config);
    if (result !== undefined) return result;
    if (path === "/capacity") return { accepting_jobs: true };
    if (path.endsWith("/ws-ticket")) return { ticket: "ticket" };
    if (path.includes("/events?")) return [];
    if (/^\/jobs\/[^/]+$/.test(path)) return { id: path.split("/").at(-1), status: "running" };
    if (path === "/uploads") return { upload_id: "upload" };
    if (path.startsWith("/auth/")) return { access_token: "token-2" };
    if (path.endsWith("connection")) return {};
    return [];
  });
});

afterEach(() => {
  if (renderer) act(() => renderer.unmount());
  renderer = null;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("analysis submission", () => {
  it.each(["telegram_chat", "upload"])("allows only one pending %s submission", async (sourceMode) => {
    await mount();
    const capacity = deferred();
    let admissionChecks = 0;
    intercept = (path) => {
      if (path === "/capacity" && admissionChecks++ === 0) return capacity.promise;
      if (path === "/jobs/telegram" || path === "/jobs") return { id: "new", status: "queued" };
    };
    const start = props("create-panel").onStartJob;
    const submission = { ...input, sourceMode, file: { name: "chat.zip", size: 10 } };
    let first, second;
    act(() => { first = start(submission); second = start(submission); });
    expect(props("create-panel").submitting).toBe(true);
    expect(admissionChecks).toBe(1);
    await act(async () => {
      capacity.resolve({ accepting_jobs: true });
      await Promise.all([first, second]);
    });
    const creations = apiJson.mock.calls.filter(([path, config]) =>
      config.method === "POST" && ["/jobs", "/jobs/telegram"].includes(path));
    expect(creations).toHaveLength(1);
    expect(uploadFileForAnalysis).toHaveBeenCalledTimes(sourceMode === "upload" ? 1 : 0);
  });

  it("releases the submission guard after an error", async () => {
    await mount();
    intercept = (path) => path === "/jobs/telegram" ? Promise.reject(new Error("offline")) : undefined;
    await act(async () => { await props("create-panel").onStartJob(input); });
    expect(props("create-panel").submitting).toBe(false);
    await act(async () => { await props("create-panel").onStartJob(input); });
    expect(apiJson.mock.calls.filter(([path]) => path === "/jobs/telegram")).toHaveLength(2);
  });

  it("disables the real form's start button while a Telegram submission is pending", async () => {
    const { CreateJobPanel } = await vi.importActual("./components/CreateJobPanel");
    const config = {
      questions: DEFAULT_QUESTIONS, options: DEFAULT_OPTIONS, questionSets: [],
      questionStatus: {}, telegramChats: [{ id: "chat", ingest_mode: "external_push" }],
      ...input, setTelegramChatId: vi.fn(), submitting: true, uploadInProgress: false,
    };
    await act(async () => { renderer = create(<CreateJobPanel {...config} />); });
    const button = renderer.root.findAllByType("button").find((item) =>
      item.children.includes("Starting analysis…"));
    expect(button.props.disabled).toBe(true);
    await act(async () => renderer.update(<CreateJobPanel {...config} submitting={false} />));
    expect(renderer.root.findAllByType("button").find((item) =>
      item.children.includes("Start analysis")).props.disabled).toBe(false);
  });
});

describe("monitor request ownership", () => {
  it("ignores status and events from the previous selection, including A → B → A", async () => {
    const status = deferred(), events = deferred();
    let delay = true;
    intercept = (path) => {
      if (delay && path === "/jobs/A") return status.promise;
      if (delay && path.startsWith("/jobs/A/events")) return events.promise;
      if (path.startsWith("/jobs/B/events")) return [{ id: 2, event_type: "report.started" }];
    };
    await mount("A");
    await act(async () => props("sidebar").onSelectJob("B"));
    expect(props("job-monitor").currentJob.id).toBe("B");
    delay = false;
    await act(async () => props("sidebar").onSelectJob("A"));
    await act(async () => {
      status.resolve({ id: "A", status: "failed" });
      events.resolve([{ id: 999, event_type: "job.failed" }]);
    });
    expect(props("job-monitor").currentJob.status).toBe("running");
    expect(props("job-monitor").events.some((event) => event.id === 999)).toBe(false);
    await act(async () => props("job-monitor").onRefresh());
    expect(apiJson.mock.calls.at(-1)[0]).toBe("/jobs/A/events?after_id=0");
  });

  it("ignores a late retry response after switching jobs", async () => {
    const retry = deferred();
    intercept = (path) => path === "/jobs/A/retry" ? retry.promise : undefined;
    await mount("A");
    let pending;
    act(() => { pending = props("job-monitor").onRetry(); });
    await act(async () => props("sidebar").onSelectJob("B"));
    await act(async () => { retry.resolve({ id: "A", status: "queued" }); await pending; });
    expect(props("job-monitor").currentJob.id).toBe("B");
  });

  it("does not expire a new login or restore old data after an old request fails", async () => {
    const status = deferred(), events = deferred();
    intercept = (path) => {
      if (path === "/jobs/A") return status.promise;
      if (path.startsWith("/jobs/A/events")) return events.promise;
    };
    await mount("A");
    await act(async () => props("sidebar").onLogout());
    await act(async () => props("login-view").onLogin({ email: "user", password: "password" }));
    await act(async () => {
      status.reject(Object.assign(new Error("Expired"), { status: 401 }));
      events.resolve([{ id: 999, event_type: "job.failed" }]);
    });
    expect(sessionStorage.getItem(STORAGE_TOKEN)).toBe("token-2");
    expect(props("sidebar").events).toEqual([]);
    expect(renderer.root.findAllByType("login-view")).toHaveLength(0);
  });

  it("ignores late socket events and ticket failures from a previous job", async () => {
    await mount("A");
    const oldSocket = sockets.at(-1);
    const lateMessage = oldSocket.onmessage;
    const ticket = deferred();
    intercept = (path) => path === "/jobs/B/ws-ticket" ? ticket.promise : undefined;
    await act(async () => props("sidebar").onSelectJob("B"));
    await act(async () => props("sidebar").onSelectJob("C"));
    await act(async () => {
      lateMessage({ data: JSON.stringify({ id: 999, event_type: "job.failed" }) });
      ticket.reject(new Error("old ticket error"));
    });
    expect(props("job-monitor").currentJob.id).toBe("C");
    expect(props("job-monitor").events).toEqual([]);
  });
});
