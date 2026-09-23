import { BAD_STATUSES, TERMINAL_STATUSES } from "../lib/constants";
import { formatDate, formatProgressPayload } from "../lib/format";
import { WorkspaceRail, WorkspaceTopbar } from "./WorkspaceChrome";

const MONITOR_PHASES = [
  { key: "prepare", label: "Prepare", stages: ["telegram_sync", "upload", "validate", "extract", "parse"] },
  { key: "understand", label: "Understand", stages: ["media", "transcription", "translation", "chunk"] },
  { key: "search", label: "Search", stages: ["embedding", "retrieval", "reranking"] },
  { key: "report", label: "Report", stages: ["answers", "report"] },
];

const EVENT_STAGE_PREFIXES = [
  ["telegram.sync", "telegram_sync"],
  ["telegram.snapshot", "telegram_sync"],
  ["upload.", "upload"],
  ["zip.scan", "validate"],
  ["zip.extract", "extract"],
  ["telegram.parse", "parse"],
  ["media.analysis", "media"],
  ["media.transcription", "transcription"],
  ["translation.", "translation"],
  ["chunking.", "chunk"],
  ["embedding.", "embedding"],
  ["retrieval.", "retrieval"],
  ["reranking.", "reranking"],
  ["answer.", "answers"],
  ["question.answer", "answers"],
  ["report.", "report"],
  ["job.completed", "report"],
];

function MonitorIcon({ name }) {
  if (name === "check") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7.5 12 3 3 6-7" /><circle cx="12" cy="12" r="9" /></svg>;
  }
  if (name === "error") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M9 9l6 6M15 9l-6 6" /></svg>;
  }
  if (name === "download") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5v11M7.75 10.5 12 14.75l4.25-4.25M4.5 19.5h15" /></svg>;
  }
  if (name === "clock") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3.25 2" /></svg>;
  }
  if (name === "refresh") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 8a7.5 7.5 0 1 0 .2 7.6M19 4.5V8h-3.5" /></svg>;
  }
  if (name === "activity") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 12h4l2.2-5.5 4.1 11 2.1-5.5h4.6" /></svg>;
  }
  if (name === "source") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3.5h8l4 4v13H6a2 2 0 0 1-2-2v-13a2 2 0 0 1 2-2Z" /><path d="M14 3.5v4h4M8 12h7M8 15.5h5" /></svg>;
  }
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5.75h14v10.5H9l-4 3z" /><path d="M8 9.25h8M8 12.75h5" /></svg>;
}

function monitorState(currentJob) {
  if (!currentJob) return "empty";
  if (currentJob.deletion_requested_at) return "working";
  if (currentJob.status === "completed") return "ready";
  if (BAD_STATUSES.has(currentJob.status)) return "attention";
  return "working";
}

function payloadRatio(payload = {}) {
  if (!payload || typeof payload !== "object") return null;
  const done = payload.done
    ?? payload.completed
    ?? payload.media_done
    ?? payload.questions_done
    ?? payload.chunks_done
    ?? payload.texts_done
    ?? payload.messages_done;
  const total = payload.total
    ?? payload.media_total
    ?? payload.questions_total
    ?? payload.chunks_total
    ?? payload.texts_total
    ?? payload.messages_total;
  if (Number.isFinite(Number(done)) && Number.isFinite(Number(total)) && Number(total) > 0) {
    return Math.min(1, Math.max(0, Number(done) / Number(total)));
  }
  if (Number.isFinite(Number(payload.progress))) {
    return Math.min(1, Math.max(0, Number(payload.progress) / 100));
  }
  return null;
}

function aggregatePhases(stageStates, currentJob) {
  return MONITOR_PHASES.map((phase) => {
    const items = stageStates.filter((item) => phase.stages.includes(item.stage.key));
    const completedCount = items.filter((item) => item.status === "completed").length;
    const failed = items.find((item) => item.status === "failed");
    const running = items.find((item) => item.status === "running");
    let status = "pending";

    if (currentJob?.status === "completed" || (items.length > 0 && completedCount === items.length)) status = "completed";
    else if (failed) status = "failed";
    else if (running) status = "running";

    const activeItem = failed || running;
    const activeRatio = payloadRatio(activeItem?.latest?.payload);
    const progressUnits = completedCount + (activeItem ? activeRatio ?? 0.16 : 0);
    const progress = items.length ? Math.min(100, (progressUnits / items.length) * 100) : 0;

    return { ...phase, items, status, progress };
  }).filter((phase) => phase.items.length > 0);
}

function activeStage(stageStates, currentJob) {
  if (!currentJob || currentJob.status === "completed") return null;
  return stageStates.find((item) => item.status === "failed")
    || stageStates.find((item) => item.status === "running")
    || stageStates.find((item) => item.status === "pending")
    || [...stageStates].reverse().find((item) => item.status === "completed")
    || null;
}

function phaseForStage(phases, stageKey) {
  return phases.find((phase) => phase.stages.includes(stageKey));
}

function activityKind(event) {
  if (event.level === "error" || event.event_type.includes("failed")) return "failed";
  if (event.event_type.includes("cancel")) return "cancelled";
  if (event.event_type.endsWith("completed")) return "completed";
  if (event.event_type.includes("retry")) return "retrying";
  if (event.event_type.endsWith("started")) return "started";
  return "progress";
}

function stageForEvent(event, stageStates) {
  const exact = stageStates.find((item) => item.stage.events.includes(event.event_type));
  if (exact) return exact.stage;
  const prefixMatch = EVENT_STAGE_PREFIXES.find(([prefix]) => event.event_type.startsWith(prefix));
  return prefixMatch ? stageStates.find((item) => item.stage.key === prefixMatch[1])?.stage : null;
}

function normalizeActivity(events, stageStates) {
  const seen = new Set();
  const activity = [];

  for (let index = events.length - 1; index >= 0 && activity.length < 4; index -= 1) {
    const event = events[index];
    if (!event?.event_type || event.event_type === "frontend") continue;
    const stage = stageForEvent(event, stageStates);
    if (!stage) continue;
    const kind = activityKind(event);
    const signature = `${stage.key}:${kind}`;
    if (seen.has(signature)) continue;
    seen.add(signature);

    const count = formatProgressPayload(event.payload);
    const suffix = {
      failed: "needs attention",
      cancelled: "cancelled",
      completed: "completed",
      retrying: "retrying",
      started: "started",
      progress: count || "in progress",
    }[kind];

    activity.push({
      id: event.id || `${event.created_at}-${event.event_type}`,
      kind,
      message: `${stage.label} · ${suffix}`,
      createdAt: event.created_at,
    });
  }

  return activity.reverse();
}

function phaseStatusLabel(status, jobStatus) {
  if (status === "completed") return "Complete";
  if (status === "running") return "In progress";
  if (status === "failed") return "Needs attention";
  if (TERMINAL_STATUSES.has(jobStatus)) return "Not reached";
  return "Waiting";
}

function focusContent(currentJob, current, currentPhase) {
  if (!currentJob) {
    return { kicker: "Analysis", title: "Loading analysis", detail: "Fetching the latest processing state." };
  }
  if (currentJob.deletion_requested_at) {
    return { kicker: "Deleting", title: "Removing analysis", detail: currentJob.cleanup_error ? "Cleanup is waiting for storage to become available. It will retry automatically." : "Stored reports and artifacts are being removed. Cleanup continues automatically if interrupted." };
  }
  if (currentJob.status === "completed") {
    return { kicker: "Complete", title: "Your report is ready", detail: "The analysis finished successfully. Choose a download package below." };
  }
  if (currentJob.status === "failed") {
    return { kicker: currentPhase?.label || "Analysis", title: "Analysis stopped", detail: currentJob.error_message || "The current processing step could not be completed." };
  }
  if (currentJob.status === "cancelled") {
    return { kicker: "Cancelled", title: "Analysis cancelled", detail: "Processing stopped at your request. Restart to create a new analysis with the same source and questions." };
  }
  if (currentJob.status === "cancelling") {
    return { kicker: currentPhase?.label || "Analysis", title: "Stopping analysis", detail: "The current operation is being stopped safely." };
  }

  const progress = formatProgressPayload(current?.latest?.payload);
  if (current?.status === "running") {
    return { kicker: currentPhase?.label || "Processing", title: current.stage.label, detail: progress || "Processing is underway." };
  }
  return {
    kicker: currentPhase?.label || "Queued",
    title: current ? `Waiting for ${current.stage.label.toLowerCase()}` : "Waiting to begin",
    detail: "The next processing step will start automatically.",
  };
}

function ActivityPanel({ activity }) {
  return (
    <section className="monitor-support-panel monitor-activity-panel" aria-labelledby="monitor-activity-title">
      <div className="monitor-support-heading">
        <span className="monitor-support-icon"><MonitorIcon name="activity" /></span>
        <div><span className="section-kicker">Live updates</span><h3 id="monitor-activity-title">Recent activity</h3></div>
      </div>
      {activity.length ? (
        <ol className="monitor-activity-list" aria-live="polite">
          {activity.map((item) => (
            <li className={`monitor-activity-item monitor-activity-${item.kind}`} key={item.id}>
              <span className="monitor-activity-marker" aria-hidden="true" />
              <span>{item.message}</span>
              <time dateTime={item.createdAt}>{new Date(item.createdAt).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}</time>
            </li>
          ))}
        </ol>
      ) : (
        <div className="monitor-activity-empty"><span className="status-dot" /> Waiting for processing updates</div>
      )}
    </section>
  );
}

function ContextPanel({ currentJob }) {
  const sourceName = currentJob?.source_name
    || (currentJob?.source_type === "telegram_chat" ? "Collected Telegram chat" : "Telegram Desktop export");
  const sourceType = currentJob?.source_type === "telegram_chat" ? "Collected chat" : "ZIP export";
  const period = currentJob?.report_start_at && currentJob?.report_end_at
    ? `${formatDate(currentJob.report_start_at)} – ${formatDate(currentJob.report_end_at)}`
    : "Full uploaded export";

  return (
    <section className="monitor-support-panel monitor-context-panel" aria-labelledby="monitor-context-title">
      <div className="monitor-support-heading">
        <span className="monitor-support-icon"><MonitorIcon name="source" /></span>
        <div><span className="section-kicker">Job context</span><h3 id="monitor-context-title">Analysis details</h3></div>
      </div>
      <dl className="monitor-context-list">
        <div><dt>Source</dt><dd title={sourceName}>{sourceName}</dd><small>{sourceType}</small></div>
        <div><dt>Report period</dt><dd>{period}</dd></div>
        <div><dt>Started</dt><dd>{currentJob ? formatDate(currentJob.created_at) : "Loading…"}</dd></div>
      </dl>
    </section>
  );
}

export function JobMonitorPanel({
  currentJobId,
  currentJob,
  stageStates,
  events = [],
  onRefresh,
  onCancel,
  onRetry,
  onDelete,
  deleteInProgress = false,
  onDownload,
  downloadInProgress,
}) {
  const state = monitorState(currentJob);
  const phases = aggregatePhases(stageStates, currentJob);
  const current = activeStage(stageStates, currentJob);
  const currentPhase = phaseForStage(phases, current?.stage?.key);
  const focus = focusContent(currentJob, current, currentPhase);
  const activity = normalizeActivity(events, stageStates);
  const deleting = Boolean(currentJob?.deletion_requested_at);
  const stateCopy = deleting ? { badge: "Deleting", title: "Removing analysis" } : {
    empty: { badge: "Loading", title: "Analysis" },
    ready: { badge: "Report ready", title: "Analysis complete" },
    attention: { badge: currentJob?.status === "cancelled" ? "Cancelled" : "Attention needed", title: "Analysis stopped" },
    working: { badge: currentJob?.status === "queued" ? "Queued" : currentJob?.status === "cancelling" ? "Stopping" : "Processing", title: "Analysis in progress" },
  }[state];

  return (
    <section className={`workspace-page workspace-page-${state} monitor-page monitor-page-${state}`}>
      <WorkspaceTopbar
        tone={state}
        badge={stateCopy.badge}
        title={currentJob?.source_name || stateCopy.title}
        subtitle={stateCopy.title}
        meta={currentJob?.scheduled_report ? <span className="monitor-scheduled-badge">Scheduled report</span> : null}
        actions={(
          <>
          <button className="button button-secondary button-small button-with-icon" type="button" onClick={onRefresh}>
            <MonitorIcon name="refresh" /> Refresh
          </button>
          {!deleting && ["failed", "cancelled"].includes(currentJob?.status) && (
            <button className="button button-primary button-small" type="button" onClick={onRetry} disabled={deleteInProgress}>{currentJob.status === "cancelled" ? "Restart analysis" : "Retry analysis"}</button>
          )}
          {!deleting && currentJob && !TERMINAL_STATUSES.has(currentJob.status) && (
            <button className="button button-ghost button-small danger-text" type="button" onClick={onCancel} disabled={currentJob.status === "cancelling"}>Cancel analysis</button>
          )}
          {!deleting && currentJob && TERMINAL_STATUSES.has(currentJob.status) && (
            <button className="button button-ghost button-small danger-text" type="button" onClick={onDelete} disabled={deleteInProgress}>{deleteInProgress ? "Requesting deletion…" : "Delete analysis"}</button>
          )}
          </>
        )}
      />

      <div className="monitor-canvas">
        <section className="monitor-focus" aria-live="polite">
          <span className={`monitor-focus-icon monitor-focus-icon-${state}`}>
            {state === "working" ? <span className="monitor-processing-ring" aria-hidden="true" /> : <MonitorIcon name={state === "ready" ? "check" : state === "attention" ? "error" : "clock"} />}
          </span>
          <div className="monitor-focus-copy">
            <span className="section-kicker">{focus.kicker}</span>
            <h1>{focus.title}</h1>
            <p>{focus.detail}</p>
          </div>
          {!deleting && currentJob?.status === "completed" && (
            <div className="monitor-download-actions" aria-label={currentJob.source_type === "upload" ? "Download all" : "Download report"}>
              <button className="button button-primary button-large button-with-icon" type="button" onClick={() => onDownload("complete")} disabled={downloadInProgress}>
                <MonitorIcon name="download" /> {downloadInProgress ? "Preparing download…" : "Complete chat + files"}
              </button>
              <button className="button button-secondary button-large" type="button" onClick={() => onDownload("reports")} disabled={downloadInProgress}>
                Main + sub-reports only
              </button>
            </div>
          )}
        </section>

        {currentJobId && currentJob ? (
          <WorkspaceRail
            className="monitor-progress-experience"
            ariaLabel="Analysis progress"
            value={stageStates.filter((item) => item.status === "completed").length}
            max={stageStates.length}
            valueText={`${focus.kicker}: ${focus.title}`}
            items={phases.map((phase) => ({
              key: phase.key,
              label: phase.label,
              status: phase.status,
              detail: phaseStatusLabel(phase.status, currentJob.status),
              progress: phase.progress,
            }))}
          />
        ) : (
          <div className="monitor-loading-track" aria-hidden="true"><span /></div>
        )}
      </div>

      <div className="monitor-support-grid">
        <ActivityPanel activity={activity} />
        <ContextPanel currentJob={currentJob} />
      </div>
    </section>
  );
}
