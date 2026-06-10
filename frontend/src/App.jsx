import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiJson, downloadBlob, uploadFileViaBackend } from "./api/client";
import { CapacityModal } from "./components/CapacityModal";
import { CreateJobPanel } from "./components/CreateJobPanel";
import { EventLogPanel } from "./components/EventLogPanel";
import { JobMonitorPanel } from "./components/JobMonitorPanel";
import { JobsRail } from "./components/JobsRail";
import { LoginView } from "./components/LoginView";
import { Toast } from "./components/Toast";
import { Topbar } from "./components/Topbar";
import { useJobSocket } from "./hooks/useJobSocket";
import { DEFAULT_OPTIONS, DEFAULT_QUESTIONS, MAX_EVENTS, STAGES, STORAGE_JOB, STORAGE_TOKEN } from "./lib/constants";
import { normalizeEvent, normalizeQuestions, optionsFromState, shortId } from "./lib/format";

function makeDerivedEvent(stage, currentJob, message = `${stage.label} abgeschlossen`) {
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
        latest: makeDerivedEvent(item.stage, currentJob, "Datei hochgeladen"),
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
  const [mainMode, setMainMode] = useState(() => (sessionStorage.getItem(STORAGE_JOB) ? "monitor" : "create"));
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);
  const [capacityOpen, setCapacityOpen] = useState(false);
  const [capacity, setCapacity] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [currentJob, setCurrentJob] = useState(null);
  const [events, setEvents] = useState([]);
  const [visibleEvents, setVisibleEvents] = useState([]);
  const [eventFilter, setEventFilter] = useState("all");
  const [wsStatus, setWsStatus] = useState("getrennt");
  const [questions, setQuestions] = useState(DEFAULT_QUESTIONS);
  const [questionStatus, setQuestionStatus] = useState({ kind: "muted", message: "Tragen Sie mindestens eine Frage ein." });
  const [options, setOptions] = useState(DEFAULT_OPTIONS);
  const [questionSets, setQuestionSets] = useState([]);
  const [selectedQuestionSetId, setSelectedQuestionSetId] = useState(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadInProgress, setUploadInProgress] = useState(false);
  const [sourceMode, setSourceMode] = useState("upload");
  const [telegramConnection, setTelegramConnection] = useState(null);
  const [telegramChats, setTelegramChats] = useState([]);
  const [telegramChatId, setTelegramChatId] = useState("");
  const [reportEnd, setReportEnd] = useState(() => localDateTimeValue(new Date()));
  const [reportStart, setReportStart] = useState(() => {
    const date = new Date();
    date.setDate(date.getDate() - 14);
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
      addLocalLog(`Kapazität konnte nicht geprüft werden: ${error.message}`, "error");
      return null;
    }
  }, [addLocalLog, request, token]);

  const refreshJobs = useCallback(async () => {
    if (!token) return;
    try {
      setJobs(await request("/jobs"));
    } catch (error) {
      addLocalLog(`Jobliste konnte nicht geladen werden: ${error.message}`, "error");
    }
  }, [addLocalLog, request, token]);

  const refreshQuestionSets = useCallback(async () => {
    if (!token) return;
    try {
      setQuestionSets(await request("/question-sets"));
    } catch (error) {
      addLocalLog(`Fragensets konnten nicht geladen werden: ${error.message}`, "warning");
    }
  }, [addLocalLog, request, token]);

  const refreshTelegram = useCallback(async () => {
    if (!token) return;
    try {
      const [connection, chats] = await Promise.all([
        request("/telegram/connection"),
        request("/telegram/chats"),
      ]);
      setTelegramConnection(connection);
      setTelegramChats(chats);
      setTelegramChatId((current) => current || chats.find((chat) => chat.status !== "archived")?.id || "");
    } catch (error) {
      addLocalLog(`Telegram-Status konnte nicht geladen werden: ${error.message}`, "warning");
    }
  }, [addLocalLog, request, token]);

  const refreshJobStatus = useCallback(async () => {
    if (!token || !currentJobId) return null;
    try {
      const job = await request(`/jobs/${currentJobId}`);
      setCurrentJob(job);
      return job;
    } catch (error) {
      addLocalLog(`Status konnte nicht geladen werden: ${error.message}`, "error");
      return null;
    }
  }, [addLocalLog, currentJobId, request, token]);

  const loadEventBacklog = useCallback(async () => {
    if (!token || !currentJobId) return;
    try {
      const backlog = await request(`/jobs/${currentJobId}/events?after_id=${lastEventIdRef.current}`);
      backlog.forEach(appendEvent);
    } catch (error) {
      addLocalLog(`Events konnten nicht geladen werden: ${error.message}`, "warning");
    }
  }, [addLocalLog, appendEvent, currentJobId, request, token]);

  const pollLatest = useCallback(async () => {
    await Promise.allSettled([refreshJobStatus(), loadEventBacklog()]);
  }, [loadEventBacklog, refreshJobStatus]);

  const selectJob = useCallback(async (jobId) => {
    setCurrentJobId(jobId);
    setMainMode("monitor");
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
      addLocalLog("Anmeldung erfolgreich");
      setMainMode(currentJobId ? "monitor" : "create");
    } catch (error) {
      showToast("Anmeldung fehlgeschlagen", "error");
      addLocalLog(`Anmeldung fehlgeschlagen: ${error.message}`, "error");
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
    resetJobEvents();
    sessionStorage.removeItem(STORAGE_TOKEN);
    sessionStorage.removeItem(STORAGE_JOB);
    showToast("Abmeldung abgeschlossen");
  };

  const startNewJobView = () => {
    setMainMode("create");
    showToast("Neue Analyse vorbereitet");
  };

  const applyQuestionSet = (questionSet) => {
    setSelectedQuestionSetId(questionSet.id);
    setQuestions(normalizeQuestions(questionSet.questions || []));
    setOptions({ ...DEFAULT_OPTIONS, ...(questionSet.default_options || {}) });
    setQuestionStatus({ kind: "success", message: `${questionSet.questions?.length || 0} Frage(n) bereit.` });
    showToast(`Fragenset geladen: ${questionSet.name}`);
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
      showToast(`Fragenset konnte nicht geladen werden: ${error.message}`, "error");
    }
  };

  const makeQuestionSetPayload = (name, description = null) => ({
    name,
    description,
    questions: normalizeQuestions(questions),
    default_options: optionsFromState(options),
  });

  const saveCurrentQuestionSet = async () => {
    try {
      const selected = questionSets.find((item) => item.id === selectedQuestionSetId);
      const name = window.prompt("Name des Fragensets", selected?.name || "Neues Fragenset");
      if (!name) return;
      const description = window.prompt("Kurzbeschreibung (optional)", selected?.description || "") || null;
      const created = await request("/question-sets", { method: "POST", body: makeQuestionSetPayload(name, description) });
      setSelectedQuestionSetId(created.id);
      await refreshQuestionSets();
      showToast(`Fragenset gespeichert: ${created.name}`);
    } catch (error) {
      showToast(`Fragenset konnte nicht gespeichert werden: ${error.message}`, "error");
    }
  };

  const updateCurrentQuestionSet = async () => {
    const selected = questionSets.find((item) => item.id === selectedQuestionSetId);
    if (!selected) {
      showToast("Bitte zuerst ein Fragenset auswählen", "warning");
      return;
    }
    try {
      const name = window.prompt("Name des Fragensets", selected.name);
      if (!name) return;
      const description = window.prompt("Kurzbeschreibung (optional)", selected.description || "") || null;
      const updated = await request(`/question-sets/${selected.id}`, { method: "PATCH", body: makeQuestionSetPayload(name, description) });
      setSelectedQuestionSetId(updated.id);
      await refreshQuestionSets();
      showToast(`Fragenset aktualisiert: ${updated.name}`);
    } catch (error) {
      showToast(`Fragenset konnte nicht aktualisiert werden: ${error.message}`, "error");
    }
  };

  const deleteCurrentQuestionSet = async () => {
    const selected = questionSets.find((item) => item.id === selectedQuestionSetId);
    if (!selected) {
      showToast("Bitte zuerst ein Fragenset auswählen", "warning");
      return;
    }
    if (!window.confirm(`Fragenset „${selected.name}“ wirklich löschen? Bestehende Analysen bleiben erhalten.`)) return;
    try {
      await request(`/question-sets/${selected.id}`, { method: "DELETE" });
      setSelectedQuestionSetId(null);
      await refreshQuestionSets();
      showToast("Fragenset gelöscht");
    } catch (error) {
      showToast(`Fragenset konnte nicht gelöscht werden: ${error.message}`, "error");
    }
  };

  const startJob = async ({ file, sourceMode: selectedSourceMode, telegramChatId: selectedChatId, reportStart: selectedStart, reportEnd: selectedEnd }) => {
    if (!token) {
      showToast("Bitte zuerst einloggen", "warning");
      return;
    }

    let normalizedQuestions;
    let normalizedOptions;
    try {
      normalizedQuestions = normalizeQuestions(questions);
      normalizedOptions = optionsFromState(options);
      setQuestionStatus({ kind: "success", message: `${normalizedQuestions.length} Frage(n) bereit.` });
    } catch (error) {
      setQuestionStatus({ kind: "error", message: error.message });
      showToast(error.message, "error");
      return;
    }

    if (selectedSourceMode === "upload") {
      if (!file) {
        showToast("Bitte eine ZIP-Datei auswählen", "warning");
        return;
      }
      if (!file.name.toLowerCase().endsWith(".zip")) {
        showToast("Bitte eine Datei im ZIP-Format auswählen", "error");
        return;
      }
    } else if (!selectedChatId || !selectedStart || !selectedEnd) {
      showToast("Bitte Chat und Berichtszeitraum auswählen", "warning");
      return;
    }

    setBusy(true);
    setUploadInProgress(selectedSourceMode === "upload");
    setUploadProgress(0);

    try {
      const currentCapacity = await refreshCapacity();
      if (currentCapacity && !currentCapacity.accepting_jobs) {
        throw new Error(`System nimmt aktuell keine neuen Jobs an: ${(currentCapacity.blockers || []).join(", ")}`);
      }

      let job;
      if (selectedSourceMode === "upload") {
        const upload = await request("/uploads", { method: "POST", body: { filename: file.name, size_bytes: file.size } });
        addLocalLog("Upload vorbereitet");
        await uploadFileViaBackend(upload, file, token, setUploadProgress);
        addLocalLog("Datei hochgeladen, Analyse wird gestartet");
        const payload = { upload_id: upload.upload_id, questions: normalizedQuestions, options: normalizedOptions };
        if (selectedQuestionSetId) payload.question_set_id = selectedQuestionSetId;
        job = await request("/jobs", { method: "POST", body: payload });
      } else {
        const startAt = new Date(selectedStart);
        const endAt = new Date(selectedEnd);
        if (!(startAt < endAt)) throw new Error("Der Beginn muss vor dem Ende liegen");
        const payload = {
          telegram_chat_id: selectedChatId,
          start_at: startAt.toISOString(),
          end_at: endAt.toISOString(),
          questions: normalizedQuestions,
          options: normalizedOptions,
        };
        if (selectedQuestionSetId) payload.question_set_id = selectedQuestionSetId;
        job = await request("/jobs/telegram", { method: "POST", body: payload });
        addLocalLog("Telegram-Synchronisierung und Analyse wurden gestartet");
      }
      await selectJob(job.id);
      addLocalLog("Analyse gestartet");
      showToast("Analyse gestartet");
      await Promise.allSettled([refreshJobs(), refreshCapacity()]);
    } catch (error) {
      showToast(error.message, "error");
      addLocalLog(`Analyse konnte nicht gestartet werden: ${error.message}`, "error");
    } finally {
      setBusy(false);
      setUploadInProgress(false);
    }
  };

  const cancelJob = async () => {
    if (!token || !currentJobId) return;
    if (!window.confirm("Analyse wirklich abbrechen?")) return;
    try {
      const data = await request(`/jobs/${currentJobId}/cancel`, { method: "POST" });
      addLocalLog(`Abbruch angefordert: ${data.status}`, "warning");
      await refreshJobStatus();
    } catch (error) {
      showToast(`Abbruch fehlgeschlagen: ${error.message}`, "error");
      addLocalLog(`Abbruch fehlgeschlagen: ${error.message}`, "error");
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
      addLocalLog("Bericht wird heruntergeladen");
    } catch (error) {
      showToast(`Bericht konnte nicht heruntergeladen werden: ${error.message}`, "error");
      addLocalLog(`Bericht konnte nicht heruntergeladen werden: ${error.message}`, "error");
    }
  };

  const copyJobId = async (jobId) => {
    await navigator.clipboard.writeText(jobId).catch(() => null);
    showToast("Interne Kennung kopiert");
  };

  const stageStates = useMemo(() => getStageState(events, currentJob), [events, currentJob]);
  const isLoggedIn = Boolean(token);
  const showMonitor = mainMode === "monitor" && Boolean(currentJobId);

  if (!isLoggedIn) {
    return (
      <>
        <LoginView onLogin={login} busy={busy} />
        <Toast toast={toast} />
      </>
    );
  }

  return (
    <div className="app-view">
      <Topbar
        isLoggedIn={isLoggedIn}
        onLogout={logout}
        onNewJob={startNewJobView}
      />

      <div className="workspace">
        <JobsRail jobs={jobs} token={token} currentJobId={currentJobId} onSelectJob={selectJob} />

        <main className="main-stage">
          {showMonitor ? (
            <JobMonitorPanel
              currentJobId={currentJobId}
              currentJob={currentJob}
              stageStates={stageStates}
              onRefresh={pollLatest}
              onCancel={cancelJob}
              onDownload={downloadReport}
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
              request={request}
              refreshTelegram={refreshTelegram}
              showToast={showToast}
            />
          )}
        </main>

        <EventLogPanel events={visibleEvents} filter={eventFilter} setFilter={setEventFilter} onClear={clearVisibleLog} />
      </div>

      <CapacityModal open={capacityOpen} capacity={capacity} onClose={() => setCapacityOpen(false)} onRefresh={refreshCapacity} />
      <Toast toast={toast} />
    </div>
  );
}
