import { BAD_STATUSES, TERMINAL_STATUSES } from "../lib/constants";
import { badgeClassForStatus, formatDate, formatProgressPayload, statusLabel } from "../lib/format";

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

function StageCard({ item, index }) {
  const { stage, status, latest } = item;
  const payloadText = status === "running" ? formatProgressPayload(latest?.payload) : "";
  const badgeClass = status === "completed" ? "badge-success" : status === "failed" ? "badge-error" : status === "running" ? "badge-warning" : "badge-muted";

  return (
    <div className={`stage stage-${status}`}>
      <div className="stage-number">{index + 1}</div>
      <div>
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
}) {
  const completed = stageStates.filter((item) => item.status === "completed").length;
  const running = stageStates.find((item) => item.status === "running");
  const failed = stageStates.find((item) => item.status === "failed");
  const current = failed || running || [...stageStates].reverse().find((item) => item.status === "completed") || stageStates[0];
  const percent = stageStates.length ? Math.round((completed / stageStates.length) * 100) : 0;
  const jobFinished = currentJob?.status === "completed";

  return (
    <section className="panel content-panel monitor-panel-guided">
      <div className="panel-header split guided-intro">
        <div>
          <span className="section-kicker">Progress</span>
          <h2>{jobFinished ? "Analysis complete" : "Analysis in progress"}</h2>
          <p>{userProgressMessage(currentJob, current)}</p>
        </div>
        <div className="inline-actions">
          <button className="button button-secondary button-small" type="button" onClick={onRefresh}>
            Refresh
          </button>
          {currentJob?.status === "failed" && (
            <button className="button button-primary button-small" type="button" onClick={onRetry}>
              Retry analysis
            </button>
          )}
          {currentJob && !TERMINAL_STATUSES.has(currentJob.status) && (
            <button className="button button-danger button-small" type="button" onClick={onCancel}>
              Cancel analysis
            </button>
          )}
        </div>
      </div>

      {!currentJobId || !currentJob ? (
        <div className="empty-state">Select an analysis from the sidebar or start a new one.</div>
      ) : (
        <div>
          <div className="job-summary-card">
            <div>
              <span className="meta-label">Status</span>
              <span className={badgeClassForStatus(currentJob.status)}>{statusLabel(currentJob.status)}</span>
            </div>
            <div>
              <span className="meta-label">Started</span>
              <span>{formatDate(currentJob.created_at)}</span>
            </div>
            {currentJob.completed_at && (
              <div>
                <span className="meta-label">Completed</span>
                <span>{formatDate(currentJob.completed_at)}</span>
              </div>
            )}
          </div>

          <div className="progress-summary-card">
            <div>
              <div className="dashboard-label">Overall progress</div>
              <div className="dashboard-value">{percent}%</div>
            </div>
            <div className="progressbar slim"><div style={{ width: `${percent}%` }} /></div>
            <div className="current-step-text">{jobFinished ? "Complete" : current?.stage?.label || "Not started"}</div>
          </div>

          {currentJob.error_message && (
            <div className="alert alert-error">
              <strong>The analysis could not be completed.</strong>
              <br />
              {currentJob.error_message}
            </div>
          )}

          {BAD_STATUSES.has(currentJob.status) && !currentJob.error_message && (
            <div className="alert alert-warning">The analysis ended with status "{statusLabel(currentJob.status)}".</div>
          )}

          <div className="stage-list user-stage-list">
            {stageStates.map((item, index) => <StageCard key={item.stage.key} item={item} index={index} />)}
          </div>

          <div className="actions-row sticky-actions">
            {currentJob.status === "completed" && (
              <button className="button button-primary button-large" type="button" onClick={onDownload}>
                Download report
              </button>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
