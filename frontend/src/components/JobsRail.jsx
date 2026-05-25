import { formatDate, badgeClassForStatus, statusLabel } from "../lib/format";

export function JobsRail({ jobs, token, currentJobId, onSelectJob }) {
  let content;

  if (!token) {
    content = <div className="jobs-list empty-state">Bitte melden Sie sich an.</div>;
  } else if (!jobs.length) {
    content = <div className="jobs-list empty-state">Noch keine Analysen vorhanden.</div>;
  } else {
    content = (
      <div className="jobs-list">
        {jobs.map((job) => (
          <button className={`job-item${job.id === currentJobId ? " is-selected" : ""}`} key={job.id} type="button" onClick={() => onSelectJob(job.id)}>
            <div>
              <strong>Analyse vom {formatDate(job.created_at)}</strong>
              <div className="hint">{statusLabel(job.status)}</div>
            </div>
            <span className={badgeClassForStatus(job.status)}>{statusLabel(job.status)}</span>
          </button>
        ))}
      </div>
    );
  }

  return (
    <aside className="left-rail panel">
      <div className="panel-header compact split">
        <div>
          <span className="section-kicker">Übersicht</span>
          <h2>Ihre Analysen</h2>
          <p className="hint compact-hint">Die Liste aktualisiert sich automatisch.</p>
        </div>
      </div>
      {content}
    </aside>
  );
}
