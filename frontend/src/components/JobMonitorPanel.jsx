import { BAD_STATUSES, TERMINAL_STATUSES } from "../lib/constants";
import { badgeClassForStatus, formatDate, formatProgressPayload, statusLabel } from "../lib/format";

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
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5.75h14v10.5H9l-4 3z" /><path d="M8 9.25h8M8 12.75h5" /></svg>;
}

function stageStatusText(status) {
  if (status === "completed") return "Completed";
  if (status === "running") return "Active";
  if (status === "failed") return "Failed";
  return "Pending";
}

function userProgressMessage(currentJob, current) {
  if (!currentJob) return "No analysis selected.";
  if (currentJob.status === "completed") return "The analysis is complete and the report is ready to download.";
  if (currentJob.status === "failed") return "The analysis could not be completed.";
  if (currentJob.status === "cancelled") return "The analysis was cancelled.";
  if (current?.status === "running") return `Current step: ${current.stage.label}.`;
  return "The analysis is waiting for the next processing step.";
}

function monitorState(currentJob) {
  if (!currentJob) return "empty";
  if (currentJob.status === "completed") return "ready";
  if (BAD_STATUSES.has(currentJob.status)) return "attention";
  return "working";
}

function StageMarker({ status, index }) {
  if (status === "completed") return <MonitorIcon name="check" />;
  if (status === "failed") return <span aria-hidden="true">!</span>;
  if (status === "running") return <span className="stage-spinner" aria-hidden="true" />;
  return <span>{index + 1}</span>;
}

function StageCard({ item, index }) {
  const { stage, status, latest } = item;
  const payloadText = status === "running" ? formatProgressPayload(latest?.payload) : "";
  const badgeClass = status === "completed" ? "badge-success" : status === "failed" ? "badge-error" : status === "running" ? "badge-warning" : "badge-muted";

  return (
    <div className={`stage stage-${status}`} aria-current={status === "running" ? "step" : undefined}>
      <div className="stage-number"><StageMarker status={status} index={index} /></div>
      <div className="stage-copy">
        <div className="stage-name">{stage.label}</div>
        <div className="stage-message">{payloadText || stageStatusText(status)}</div>
      </div>
      <span className={`badge ${badgeClass}`}>{stageStatusText(status)}</span>
    </div>
  );
}

export function JobMonitorPanel({
  currentJobId,
  currentJob,
  stageStates,
  onRefresh,
  onCancel,
  onRetry,
  onDownload,
  downloadInProgress,
}) {
  const completed = stageStates.filter((item) => item.status === "completed").length;
  const running = stageStates.find((item) => item.status === "running");
  const failed = stageStates.find((item) => item.status === "failed");
  const current = failed || running || [...stageStates].reverse().find((item) => item.status === "completed") || stageStates[0];
  const percent = stageStates.length ? Math.round((completed / stageStates.length) * 100) : 0;
  const jobFinished = currentJob?.status === "completed";
  const state = monitorState(currentJob);
  const stateCopy = {
    empty: { badge: "No selection", title: "Choose an analysis", icon: "chat" },
    ready: { badge: "Report ready", title: "Analysis complete", icon: "check" },
    attention: { badge: "Attention needed", title: currentJob?.status === "cancelled" ? "Analysis cancelled" : "Analysis stopped", icon: "error" },
    working: { badge: "Processing", title: "Analysis in progress", icon: "clock" },
  }[state];

  return (
    <section className="monitor-page">
      <header className={`monitor-overview monitor-overview-${state}`}>
        <div className="monitor-readiness">
          <span className="monitor-hero-icon"><MonitorIcon name={stateCopy.icon} /></span>
          <div>
            <span className={`monitor-state-badge monitor-state-badge-${state}`}><span className="status-dot" />{stateCopy.badge}</span>
            <span className="section-kicker">Progress</span>
            <h2>{stateCopy.title}</h2>
            <p>{userProgressMessage(currentJob, current)}</p>
          </div>
        </div>
        <div className="monitor-overview-actions">
          <button className="button button-secondary button-small button-with-icon" type="button" onClick={onRefresh}>
            <MonitorIcon name="refresh" /> Refresh
          </button>
          {currentJob?.status === "failed" && (
            <button className="button button-primary button-small" type="button" onClick={onRetry}>Retry analysis</button>
          )}
          {currentJob && !TERMINAL_STATUSES.has(currentJob.status) && (
            <button className="button button-ghost button-small danger-text" type="button" onClick={onCancel}>Cancel analysis</button>
          )}
        </div>
      </header>

      {!currentJobId || !currentJob ? (
        <div className="surface monitor-empty-state">
          <span className="monitor-empty-icon"><MonitorIcon name="chat" /></span>
          <div><h3>No analysis selected</h3><p>Select a recent analysis from the sidebar or start a new one.</p></div>
        </div>
      ) : (
        <>
          <section className="monitor-metrics" aria-label="Analysis summary">
            <div className="monitor-metric">
              <span className="monitor-metric-icon"><MonitorIcon name="clock" /></span>
              <div><span>Status</span><strong>{statusLabel(currentJob.status)}</strong></div>
            </div>
            <div className="monitor-metric">
              <span className="monitor-metric-icon"><MonitorIcon name="refresh" /></span>
              <div><span>Current step</span><strong>{jobFinished ? "Complete" : current?.stage?.label || "Waiting"}</strong></div>
            </div>
            <div className="monitor-metric">
              <span className="monitor-metric-icon"><MonitorIcon name="check" /></span>
              <div><span>Steps complete</span><strong>{completed} of {stageStates.length}</strong></div>
            </div>
          </section>

          <section className="surface monitor-progress-card progress-summary-card">
            <div className="monitor-progress-heading">
              <div><span className="section-kicker">Overall progress</span><strong>{percent}%</strong></div>
              <span className={badgeClassForStatus(currentJob.status)}>{statusLabel(currentJob.status)}</span>
            </div>
            <div className="progressbar"><div style={{ width: `${percent}%` }} /></div>
            <div className="monitor-progress-dates">
              <span>Started {formatDate(currentJob.created_at)}</span>
              {currentJob.completed_at && <span>Completed {formatDate(currentJob.completed_at)}</span>}
            </div>
          </section>

          {currentJob.error_message && (
            <div className="alert alert-error monitor-alert" role="alert">
              <span className="alert-icon"><MonitorIcon name="error" /></span>
              <div><strong>The analysis could not be completed.</strong><p>{currentJob.error_message}</p></div>
            </div>
          )}

          {BAD_STATUSES.has(currentJob.status) && !currentJob.error_message && (
            <div className="alert alert-warning monitor-alert"><span className="alert-icon">!</span><div>The analysis ended with status "{statusLabel(currentJob.status)}".</div></div>
          )}

          <section className="surface monitor-stages-card">
            <div className="monitor-section-heading">
              <div><span className="section-kicker">Pipeline</span><h3>Processing steps</h3></div>
              <span className="stage-count">{completed}/{stageStates.length}</span>
            </div>
            <div className="stage-list user-stage-list">
              {stageStates.map((item, index) => <StageCard key={item.stage.key} item={item} index={index} />)}
            </div>
          </section>

          {currentJob.status === "completed" && (
            <section className="surface report-ready-card">
              <span className="report-ready-icon"><MonitorIcon name="download" /></span>
              <div className="report-ready-copy">
                <span className="section-kicker">Downloads</span>
                <h3>Your report is ready</h3>
                <p>{currentJob.source_type === "upload" ? "Download the report together with the original export." : "Download the finished analysis report."}</p>
              </div>
              <div className="actions-row report-ready-actions">
                <button className="button button-primary button-large" type="button" onClick={onDownload} disabled={downloadInProgress}>
                  {downloadInProgress ? "Preparing download…" : currentJob.source_type === "upload" ? "Download all" : "Download report"}
                </button>
              </div>
            </section>
          )}
        </>
      )}
    </section>
  );
}
