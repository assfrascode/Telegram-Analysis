import { EventLogPanel } from "./EventLogPanel";
import { formatDate, statusLabel } from "../lib/format";

function jobTitle(job, chats) {
  if (job.source_type !== "telegram_chat") return "Uploaded export";
  return chats.find((chat) => chat.id === job.telegram_chat_id)?.title || "Telegram chat";
}

function AnalysisList({ jobs, chats, currentJobId, onSelectJob }) {
  if (!jobs.length) {
    return <div className="sidebar-empty">No analyses yet.</div>;
  }

  return (
    <div className="analysis-list">
      {jobs.map((job) => {
        const label = statusLabel(job.status);
        return (
          <button
            className={`analysis-list-item status-border-${job.status}${job.id === currentJobId ? " is-selected" : ""}`}
            key={job.id}
            type="button"
            onClick={() => onSelectJob(job.id)}
            aria-label={`${jobTitle(job, chats)}, ${formatDate(job.created_at)}, ${label}`}
            title={label}
          >
            <span className={`status-dot status-dot-${job.status}`} aria-hidden="true" />
            <span className="analysis-list-copy">
              <strong>{jobTitle(job, chats)}</strong>
              <span>{formatDate(job.created_at)}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

export function AppSidebar({
  activeView,
  jobs,
  chats,
  currentJobId,
  events,
  eventFilter,
  setEventFilter,
  onClearEvents,
  capacity,
  onRefreshCapacity,
  onNavigate,
  onSelectJob,
  onLogout,
}) {
  return (
    <aside className="app-sidebar">
      <div className="sidebar-brand">
        <span className="app-mark">CA</span>
        <div>
          <strong>Chat Analysis</strong>
          <span>Telegram intelligence workspace</span>
        </div>
      </div>

      <nav className="primary-nav" aria-label="Main navigation">
        <button
          className={`nav-item${activeView === "analysis" ? " is-active" : ""}`}
          type="button"
          onClick={() => onNavigate("analysis")}
        >
          <span className="nav-icon">+</span>
          New Analysis
        </button>
        <button
          className={`nav-item${activeView === "telegram" ? " is-active" : ""}`}
          type="button"
          onClick={() => onNavigate("telegram")}
        >
          <span className="nav-icon">T</span>
          Telegram Setup
        </button>
      </nav>

      <section className="sidebar-history">
        <div className="sidebar-section-heading">
          <span>Recent analyses</span>
          <span>{jobs.length}</span>
        </div>
        <AnalysisList jobs={jobs} chats={chats} currentJobId={currentJobId} onSelectJob={onSelectJob} />
      </section>

      <div className="sidebar-utilities">
        <EventLogPanel
          events={events}
          filter={eventFilter}
          setFilter={setEventFilter}
          onClear={onClearEvents}
          capacity={capacity}
          onRefreshCapacity={onRefreshCapacity}
        />
        <button className="sidebar-logout" type="button" onClick={onLogout}>
          Sign out
        </button>
      </div>
    </aside>
  );
}
