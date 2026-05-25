import { BAD_STATUSES, TERMINAL_STATUSES } from "../lib/constants";
import { badgeClassForStatus, formatDate, formatProgressPayload, statusLabel } from "../lib/format";

function stageStatusText(status) {
  if (status === "completed") return "Erledigt";
  if (status === "running") return "Aktiv";
  if (status === "failed") return "Fehler";
  return "Offen";
}

function userProgressMessage(currentJob, current) {
  if (!currentJob) return "Noch keine Analyse ausgewählt.";
  if (currentJob.status === "completed") return "Die Analyse ist abgeschlossen. Der Bericht steht zum Herunterladen bereit.";
  if (currentJob.status === "failed") return "Die Analyse konnte nicht abgeschlossen werden.";
  if (currentJob.status === "cancelled") return "Die Analyse wurde abgebrochen.";
  if (current?.status === "running") return `Aktueller Schritt: ${current.stage.label}.`;
  return "Die Analyse wartet auf den nächsten Verarbeitungsschritt.";
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
          <span className="section-kicker">Fortschritt</span>
          <h2>{jobFinished ? "Analyse abgeschlossen" : "Analyse wird verarbeitet"}</h2>
          <p>{userProgressMessage(currentJob, current)}</p>
        </div>
        <div className="inline-actions">
          <button className="button button-secondary button-small" type="button" onClick={onRefresh}>
            Aktualisieren
          </button>
          {currentJob && !TERMINAL_STATUSES.has(currentJob.status) && (
            <button className="button button-danger button-small" type="button" onClick={onCancel}>
              Analyse abbrechen
            </button>
          )}
        </div>
      </div>

      {!currentJobId || !currentJob ? (
        <div className="empty-state">Wählen Sie links eine Analyse aus oder starten Sie eine neue Analyse.</div>
      ) : (
        <div>
          <div className="job-summary-card">
            <div>
              <span className="meta-label">Status</span>
              <span className={badgeClassForStatus(currentJob.status)}>{statusLabel(currentJob.status)}</span>
            </div>
            <div>
              <span className="meta-label">Gestartet</span>
              <span>{formatDate(currentJob.created_at)}</span>
            </div>
            {currentJob.completed_at && (
              <div>
                <span className="meta-label">Abgeschlossen</span>
                <span>{formatDate(currentJob.completed_at)}</span>
              </div>
            )}
          </div>

          <div className="progress-summary-card">
            <div>
              <div className="dashboard-label">Gesamtfortschritt</div>
              <div className="dashboard-value">{percent}%</div>
            </div>
            <div className="progressbar slim"><div style={{ width: `${percent}%` }} /></div>
            <div className="current-step-text">{jobFinished ? "Fertig" : current?.stage?.label || "Noch nicht gestartet"}</div>
          </div>

          {currentJob.error_message && (
            <div className="alert alert-error">
              <strong>Die Analyse konnte nicht abgeschlossen werden.</strong>
              <br />
              {currentJob.error_message}
            </div>
          )}

          {BAD_STATUSES.has(currentJob.status) && !currentJob.error_message && (
            <div className="alert alert-warning">Die Analyse wurde mit Status „{statusLabel(currentJob.status)}“ beendet.</div>
          )}

          <div className="stage-list user-stage-list">
            {stageStates.map((item, index) => <StageCard key={item.stage.key} item={item} index={index} />)}
          </div>

          <div className="actions-row sticky-actions">
            {currentJob.status === "completed" && (
              <button className="button button-primary button-large" type="button" onClick={onDownload}>
                Bericht herunterladen
              </button>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
