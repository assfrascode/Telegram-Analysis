import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiJson, downloadBlob, uploadFileForAnalysis } from "./api/client";
import { AppSidebar } from "./components/AppSidebar";
import { CreateJobPanel } from "./components/CreateJobPanel";
import { JobMonitorPanel } from "./components/JobMonitorPanel";
import { LoginView } from "./components/LoginView";
import { TelegramSourcesPanel } from "./components/TelegramSourcesPanel";
import { Toast } from "./components/Toast";
import { useJobSocket } from "./hooks/useJobSocket";
import { DEFAULT_OPTIONS, DEFAULT_QUESTIONS, MAX_EVENTS, STAGES, STORAGE_JOB, STORAGE_TOKEN } from "./lib/constants";
import { normalizeEvent, normalizeQuestions, optionsFromState, shortId } from "./lib/format";

function makeDerivedEvent(stage, currentJob, message = `${stage.label} completed`) {
  return {
    event_type: `${stage.key}.completed`,
    level: "info",
    message,
    created_at: currentJob?.completed_at || currentJob?.updated_at || currentJob?.created_at || new Date().toISOString(),
  };
}

function localDateTimeValue(date) {
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

const MAX_TELEGRAM_REPORT_DAYS = 30;

function requestErrorMessage(reason) {
  return reason?.message || String(reason);
}

function getStageState(events, currentJob) {
  const applicableStages = STAGES.filter((stage) => {
    if (currentJob?.source_type === "telegram_chat") {
      return !["upload", "validate", "extract", "parse"].includes(stage.key);
    }
    return stage.key !== "telegram_sync";
  });
  const rawStates = applicableStages.map((stage) => {
    const matching = events.filter((event) => stage.events.includes(event.event_type));
    const latest = matching.at(-1);
    const completed = matching.some((event) => /completed$/.test(event.event_type) || event.event_type === "job.completed");
    const started = Boolean(matching.length);
    const failed = matching.some((event) => event.level === "error" || event.event_type.includes("failed"));

    let status = "pending";
    if (failed) status = "failed";
    else if (completed) status = "completed";
    else if (started) status = "running";

    return { stage, latest, started, completed, failed, status, derived: false };
  });

  const hasCompletedJobEvent = events.some((event) => event.event_type === "job.completed");
  const jobCompleted = currentJob?.status === "completed" || hasCompletedJobEvent;

  const lastObservedIndex = rawStates.reduce((latestIndex, item, index) => {
    if (item.started || item.completed || item.failed) return index;
    return latestIndex;
  }, -1);

  return rawStates.map((item, index) => {
    // Upload is completed before the backend job is created, so normal job
    // event backlogs do not reliably contain an `upload.completed` event.
    // Once a job exists, the upload necessarily succeeded.
    if (item.stage.key === "upload" && currentJob?.id && item.status === "pending") {
      return {
        ...item,
        latest: makeDerivedEvent(item.stage, currentJob, "File uploaded"),
        started: true,
        completed: true,
        failed: false,
        status: "completed",
        derived: true,
      };
    }

    // The backend can skip or rename low-level housekeeping events such as
    // ZIP extraction while still emitting later pipeline stages. If a later
    // stage has already started, earlier untouched stages must have succeeded.
    // Otherwise the dashboard gets stuck at e.g. 10/11 despite a ready report.
    if (item.status === "pending" && (jobCompleted || index < lastObservedIndex)) {
      return {
        ...item,
        latest: makeDerivedEvent(item.stage, currentJob),
        started: true,
        completed: true,
        failed: false,
        status: "completed",
        derived: true,
      };
    }

    return item;
  });
}

export default function App() {
  const [token, setToken] = useState(() => sessionStorage.getItem(STORAGE_TOKEN));
  const [currentJobId, setCurrentJobId] = useState(() => sessionStorage.getItem(STORAGE_JOB));
  const [activeView, setActiveView] = useState(() => (sessionStorage.getItem(STORAGE_JOB) ? "monitor" : "analysis"));
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);
  const [capacity, setCapacity] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [currentJob, setCurrentJob] = useState(null);
  const [events, setEvents] = useState([]);
  const [visibleEvents, setVisibleEvents] = useState([]);
  const [eventFilter, setEventFilter] = useState("all");
  const [, setWsStatus] = useState("disconnected");
  const [questions, setQuestions] = useState(DEFAULT_QUESTIONS);
  const [questionStatus, setQuestionStatus] = useState({ kind: "muted", message: "" });
  const [options, setOptions] = useState(DEFAULT_OPTIONS);
  const [questionSets, setQuestionSets] = useState([]);
  const [selectedQuestionSetId, setSelectedQuestionSetId] = useState(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadInProgress, setUploadInProgress] = useState(false);
  const [sourceMode, setSourceMode] = useState("upload");
  const [telegramConnection, setTelegramConnection] = useState(null);
  const [telegramChats, setTelegramChats] = useState([]);
  const [telegramReportSchedules, setTelegramReportSchedules] = useState([]);
  const [telegramChatId, setTelegramChatId] = useState("");
  const [reportEnd, setReportEnd] = useState(() => localDateTimeValue(new Date()));
  const [reportStart, setReportStart] = useState(() => {
    const date = new Date();
    date.setTime(date.getTime() - MAX_TELEGRAM_REPORT_DAYS * 24 * 60 * 60 * 1000);
    return localDateTimeValue(date);
  });

  const eventIdsRef = useRef(new Set());
  const lastEventIdRef = useRef(0);

  const showToast = useCallback((message, kind = "info") => {
    setToast({ message, kind, createdAt: Date.now() });
  }, []);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(null), 4500);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const request = useCallback((path, config = {}) => apiJson(path, { ...config, token }), [token]);

  const appendEvent = useCallback((event) => {
    const normalized = normalizeEvent(event);
    if (normalized.id && eventIdsRef.current.has(normalized.id)) return false;

    if (normalized.id) {
      eventIdsRef.current.add(normalized.id);
      lastEventIdRef.current = Math.max(lastEventIdRef.current, normalized.id);
    }

    setEvents((current) => {
      const next = [...current, normalized];
      while (next.length > MAX_EVENTS) {
        const removed = next.shift();
        if (removed?.id) eventIdsRef.current.delete(removed.id);
      }
      return next;
    });
    setVisibleEvents((current) => {
      const next = [...current, normalized];
      while (next.length > MAX_EVENTS) next.shift();
      return next;
    });
    return true;
  }, []);

  const resetJobEvents = useCallback(() => {
    eventIdsRef.current = new Set();
    lastEventIdRef.current = 0;
    setEvents([]);
    setVisibleEvents([]);
  }, []);

  const clearVisibleLog = useCallback(() => {
    setVisibleEvents([]);
  }, []);

  const addLocalLog = useCallback((message, level = "info") => {
    appendEvent({ event_type: "frontend", level, message, created_at: new Date().toISOString() });
  }, [appendEvent]);

  const refreshCapacity = useCallback(async () => {
    if (!token) return null;
    try {
      const data = await request("/capacity");
      setCapacity(data);
      return data;
    } catch (error) {
      const fallback = { accepting_jobs: false, blockers: ["capacity_request_failed"], resources: {}, error: error.message };
      setCapacity(fallback);
      addLocalLog(`Could not check system capacity: ${error.message}`, "error");
      return null;
    }
  }, [addLocalLog, request, token]);

  const refreshJobs = useCallback(async () => {
    if (!token) return;
    try {
      setJobs(await request("/jobs"));
    } catch (error) {
      addLocalLog(`Could not load analyses: ${error.message}`, "error");
    }
  }, [addLocalLog, request, token]);

  const refreshQuestionSets = useCallback(async () => {
    if (!token) return;
    try {
      setQuestionSets(await request("/question-sets"));
    } catch (error) {
      addLocalLog(`Could not load question sets: ${error.message}`, "warning");
    }
  }, [addLocalLog, request, token]);

  const refreshTelegram = useCallback(async () => {
    if (!token) return;
    const [connectionResult, chatsResult, schedulesResult] = await Promise.allSettled([
      request("/telegram/connection"),
      request("/telegram/chats"),
      request("/telegram/report-schedules"),
    ]);

    if (connectionResult.status === "fulfilled") {
      setTelegramConnection(connectionResult.value);
    } else {
      addLocalLog(`Could not load Telegram connection: ${requestErrorMessage(connectionResult.reason)}`, "warning");
    }

    if (chatsResult.status === "fulfilled") {
      const chats = chatsResult.value;
      setTelegramChats(chats);
      setTelegramChatId((current) => (
        chats.some((chat) => chat.id === current && chat.status !== "archived")
          ? current
          : chats.find((chat) => chat.status !== "archived")?.id || ""
      ));
    } else {
      addLocalLog(`Could not load Telegram chats: ${requestErrorMessage(chatsResult.reason)}`, "warning");
    }

    if (schedulesResult.status === "fulfilled") {
      setTelegramReportSchedules(schedulesResult.value);
    } else {
      addLocalLog(`Could not load Telegram report schedules: ${requestErrorMessage(schedulesResult.reason)}`, "warning");
    }
  }, [addLocalLog, request, token]);

  const refreshJobStatus = useCallback(async () => {
    if (!token || !currentJobId) return null;
    try {
      const job = await request(`/jobs/${currentJobId}`);
      setCurrentJob(job);
      return job;
    } catch (error) {
      addLocalLog(`Could not load analysis status: ${error.message}`, "error");
      return null;
    }
  }, [addLocalLog, currentJobId, request, token]);

  const loadEventBacklog = useCallback(async () => {
    if (!token || !currentJobId) return;
    try {
      const backlog = await request(`/jobs/${currentJobId}/events?after_id=${lastEventIdRef.current}`);
      backlog.forEach(appendEvent);
    } catch (error) {
      addLocalLog(`Could not load events: ${error.message}`, "warning");
    }
  }, [addLocalLog, appendEvent, currentJobId, request, token]);

  const pollLatest = useCallback(async () => {
    await Promise.allSettled([refreshJobStatus(), loadEventBacklog()]);
  }, [loadEventBacklog, refreshJobStatus]);

  const selectJob = useCallback(async (jobId) => {
    setCurrentJobId(jobId);
    setActiveView("monitor");
    sessionStorage.setItem(STORAGE_JOB, jobId);
    resetJobEvents();
  }, [resetJobEvents]);

  useEffect(() => {
    if (!token || !currentJobId) return;
    pollLatest();
  }, [currentJobId, pollLatest, token]);

  useJobSocket({
    token,
    currentJobId,
    currentJobStatus: currentJob?.status,
    appendEvent,
    pollLatest,
    setWsStatus,
    onTerminalEvent: () => {
      window.setTimeout(() => pollLatest(), 100);
      window.setTimeout(() => refreshJobs(), 250);
      window.setTimeout(() => refreshCapacity(), 500);
    },
  });

  useEffect(() => {
    if (!token) return;
    Promise.allSettled([refreshCapacity(), refreshJobs(), refreshQuestionSets(), refreshTelegram()]);
  }, [refreshCapacity, refreshJobs, refreshQuestionSets, refreshTelegram, token]);


  useEffect(() => {
    if (!token) return undefined;
    const timer = window.setInterval(() => {
      refreshJobs();
      refreshQuestionSets();
      refreshTelegram();
    }, 30000);
    return () => window.clearInterval(timer);
  }, [refreshJobs, refreshQuestionSets, refreshTelegram, token]);

  const login = async ({ email, password }) => {
    setBusy(true);
    try {
      const data = await apiJson("/auth/login", { method: "POST", body: { email, password } });
      setToken(data.access_token);
      sessionStorage.setItem(STORAGE_TOKEN, data.access_token);
      addLocalLog("Signed in");
      setActiveView(currentJobId ? "monitor" : "analysis");
    } catch (error) {
      showToast("Sign-in failed", "error");
      addLocalLog(`Sign-in failed: ${error.message}`, "error");
    } finally {
      setBusy(false);
    }
  };

  const register = async ({ email, password }) => {
    setBusy(true);
    try {
      const data = await apiJson("/auth/register", { method: "POST", body: { email, password } });
      setToken(data.access_token);
      sessionStorage.setItem(STORAGE_TOKEN, data.access_token);
      addLocalLog("Account created");
      showToast("Account created");
      setActiveView(currentJobId ? "monitor" : "analysis");
    } catch (error) {
      showToast("Registration failed", "error");
      addLocalLog(`Registration failed: ${error.message}`, "error");
    } finally {
      setBusy(false);
    }
  };

  const logout = () => {
    setToken(null);
    setCurrentJobId(null);
    setCurrentJob(null);
    setQuestionSets([]);
    setSelectedQuestionSetId(null);
    setCapacity(null);
    setTelegramConnection(null);
    setTelegramChats([]);
    setTelegramReportSchedules([]);
    resetJobEvents();
    sessionStorage.removeItem(STORAGE_TOKEN);
    sessionStorage.removeItem(STORAGE_JOB);
    setActiveView("analysis");
    showToast("Signed out");
  };

  const openTelegramSetup = () => setActiveView("telegram");

  const applyQuestionSet = (questionSet) => {
    setSelectedQuestionSetId(questionSet.id);
    setQuestions(normalizeQuestions(questionSet.questions || []));
    setOptions({ ...DEFAULT_OPTIONS, ...(questionSet.default_options || {}) });
    setQuestionStatus({ kind: "muted", message: "" });
    showToast(`Question set loaded: ${questionSet.name}`);
  };

  const selectAndLoadQuestionSet = async (questionSetId) => {
    setSelectedQuestionSetId(questionSetId || null);
    if (!questionSetId) return;
    try {
      const questionSet = await request(`/question-sets/${questionSetId}`);
      setQuestionSets((current) => {
        const existing = current.findIndex((item) => item.id === questionSet.id);
        if (existing === -1) return [...current, questionSet];
        const next = [...current];
        next[existing] = questionSet;
        return next;
      });
      applyQuestionSet(questionSet);
    } catch (error) {
      showToast(`Could not load question set: ${error.message}`, "error");
    }
  };

  const makeQuestionSetPayload = (name, description = null) => ({
    name,
    description,
    questions: normalizeQuestions(questions),
    default_options: optionsFromState(options),
  });

  const saveCurrentQuestionSet = async ({ name, description }) => {
    try {
      const created = await request("/question-sets", {
        method: "POST",
        body: makeQuestionSetPayload(name, description || null),
      });
      setSelectedQuestionSetId(created.id);
      await refreshQuestionSets();
      showToast(`Question set saved: ${created.name}`);
      return created;
    } catch (error) {
      showToast(`Could not save question set: ${error.message}`, "error");
      throw error;
    }
  };

  const updateCurrentQuestionSet = async ({ name, description }) => {
    const selected = questionSets.find((item) => item.id === selectedQuestionSetId);
    if (!selected) {
      showToast("Select a question set first", "warning");
      return;
    }
    try {
      const updated = await request(`/question-sets/${selected.id}`, {
        method: "PATCH",
        body: makeQuestionSetPayload(name, description || null),
      });
      setSelectedQuestionSetId(updated.id);
      await refreshQuestionSets();
      showToast(`Question set updated: ${updated.name}`);
      return updated;
    } catch (error) {
      showToast(`Could not update question set: ${error.message}`, "error");
      throw error;
    }
  };

  const deleteCurrentQuestionSet = async () => {
    const selected = questionSets.find((item) => item.id === selectedQuestionSetId);
    if (!selected) {
      showToast("Select a question set first", "warning");
      return;
    }
    try {
      await request(`/question-sets/${selected.id}`, { method: "DELETE" });
      setSelectedQuestionSetId(null);
      await refreshQuestionSets();
      showToast("Question set deleted");
    } catch (error) {
      showToast(`Could not delete question set: ${error.message}`, "error");
      throw error;
    }
  };

  const startJob = async ({ file, sourceMode: selectedSourceMode, telegramChatId: selectedChatId, reportStart: selectedStart, reportEnd: selectedEnd }) => {
    if (!token) {
      showToast("Sign in first", "warning");
      return;
    }

    let normalizedQuestions;
    let normalizedOptions;
    try {
      normalizedQuestions = normalizeQuestions(questions);
      normalizedOptions = optionsFromState(options);
      setQuestionStatus({ kind: "muted", message: "" });
    } catch (error) {
      setQuestionStatus({ kind: "error", message: error.message });
      showToast(error.message, "error");
      return;
    }

    if (selectedSourceMode === "upload") {
      if (!file) {
        showToast("Select a ZIP file", "warning");
        return;
      }
      if (!file.name.toLowerCase().endsWith(".zip")) {
        showToast("Select a file in ZIP format", "error");
        return;
      }
    } else if (!selectedChatId || !selectedStart || !selectedEnd) {
      showToast("Select a chat and reporting period", "warning");
      return;
    }

    setBusy(true);
    setUploadInProgress(selectedSourceMode === "upload");
    setUploadProgress(0);

    try {
      const currentCapacity = await refreshCapacity();
      if (currentCapacity && !currentCapacity.accepting_jobs) {
        throw new Error(`The system is not accepting new analyses: ${(currentCapacity.blockers || []).join(", ")}`);
      }

      let job;
      if (selectedSourceMode === "upload") {
        const upload = await request("/uploads", { method: "POST", body: { filename: file.name, size_bytes: file.size } });
        addLocalLog("Upload prepared");
        await uploadFileForAnalysis(upload, file, token, setUploadProgress);
        addLocalLog("File uploaded, starting analysis");
        const payload = { upload_id: upload.upload_id, questions: normalizedQuestions, options: normalizedOptions };
        if (selectedQuestionSetId) payload.question_set_id = selectedQuestionSetId;
        job = await request("/jobs", { method: "POST", body: payload });
      } else {
        const startAt = new Date(selectedStart);
        const endAt = new Date(selectedEnd);
        if (!(startAt < endAt)) throw new Error("The start time must be before the end time");
        if (endAt.getTime() - startAt.getTime() > MAX_TELEGRAM_REPORT_DAYS * 24 * 60 * 60 * 1000) {
          throw new Error(`Telegram reports can cover at most ${MAX_TELEGRAM_REPORT_DAYS} days`);
        }
        const payload = {
          telegram_chat_id: selectedChatId,
          start_at: startAt.toISOString(),
          end_at: endAt.toISOString(),
          questions: normalizedQuestions,
          options: normalizedOptions,
        };
        if (selectedQuestionSetId) payload.question_set_id = selectedQuestionSetId;
        job = await request("/jobs/telegram", { method: "POST", body: payload });
        addLocalLog("Telegram synchronization and analysis started");
      }
      await selectJob(job.id);
      addLocalLog("Analysis started");
      showToast("Analysis started");
      await Promise.allSettled([refreshJobs(), refreshCapacity()]);
    } catch (error) {
      showToast(error.message, "error");
      addLocalLog(`Could not start analysis: ${error.message}`, "error");
    } finally {
      setBusy(false);
      setUploadInProgress(false);
    }
  };

  const cancelJob = async () => {
    if (!token || !currentJobId) return;
    if (!window.confirm("Cancel this analysis?")) return;
    try {
      const data = await request(`/jobs/${currentJobId}/cancel`, { method: "POST" });
      addLocalLog(`Cancellation requested: ${data.status}`, "warning");
      await refreshJobStatus();
    } catch (error) {
      showToast(`Cancellation failed: ${error.message}`, "error");
      addLocalLog(`Cancellation failed: ${error.message}`, "error");
    }
  };

  const downloadReport = async () => {
    if (!token || !currentJobId) return;
    try {
      const blob = await downloadBlob(`/jobs/${currentJobId}/report/download`, { token });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `report-${shortId(currentJobId)}.zip`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      addLocalLog("Report download started");
    } catch (error) {
      showToast(`Could not download report: ${error.message}`, "error");
      addLocalLog(`Could not download report: ${error.message}`, "error");
    }
  };

  const stageStates = useMemo(() => getStageState(events, currentJob), [events, currentJob]);
  const isLoggedIn = Boolean(token);
  const showMonitor = activeView === "monitor" && Boolean(currentJobId);

  if (!isLoggedIn) {
    return (
      <>
        <LoginView onLogin={login} onRegister={register} busy={busy} />
        <Toast toast={toast} />
      </>
    );
  }

  return (
    <div className="app-shell">
      <AppSidebar
        activeView={activeView}
        jobs={jobs}
        chats={telegramChats}
        currentJobId={currentJobId}
        events={visibleEvents}
        eventFilter={eventFilter}
        setEventFilter={setEventFilter}
        onClearEvents={clearVisibleLog}
        capacity={capacity}
        onRefreshCapacity={refreshCapacity}
        onNavigate={setActiveView}
        onSelectJob={selectJob}
        onLogout={logout}
      />

      <main className="app-content">
        {showMonitor ? (
            <JobMonitorPanel
              currentJobId={currentJobId}
              currentJob={currentJob}
              stageStates={stageStates}
              onRefresh={pollLatest}
              onCancel={cancelJob}
              onDownload={downloadReport}
            />
        ) : activeView === "telegram" ? (
            <TelegramSourcesPanel
              connection={telegramConnection}
              chats={telegramChats}
              schedules={telegramReportSchedules}
              questionSets={questionSets}
              request={request}
              onRefresh={refreshTelegram}
              onSelectJob={selectJob}
              showToast={showToast}
            />
        ) : (
            <CreateJobPanel
              questions={questions}
              setQuestions={setQuestions}
              options={options}
              setOptions={setOptions}
              questionStatus={questionStatus}
              setQuestionStatus={setQuestionStatus}
              questionSets={questionSets}
              selectedQuestionSetId={selectedQuestionSetId}
              setSelectedQuestionSetId={setSelectedQuestionSetId}
              uploadProgress={uploadProgress}
              uploadInProgress={uploadInProgress}
              onStartJob={startJob}
              onSelectQuestionSet={selectAndLoadQuestionSet}
              onSaveQuestionSet={saveCurrentQuestionSet}
              onUpdateQuestionSet={updateCurrentQuestionSet}
              onDeleteQuestionSet={deleteCurrentQuestionSet}
              sourceMode={sourceMode}
              setSourceMode={setSourceMode}
              telegramConnection={telegramConnection}
              telegramChats={telegramChats}
              telegramChatId={telegramChatId}
              setTelegramChatId={setTelegramChatId}
              reportStart={reportStart}
              setReportStart={setReportStart}
              reportEnd={reportEnd}
              setReportEnd={setReportEnd}
              onOpenTelegram={openTelegramSetup}
            />
        )}
      </main>
      <Toast toast={toast} />
    </div>
  );
}
