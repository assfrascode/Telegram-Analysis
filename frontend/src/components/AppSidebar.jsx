import { EventLogPanel } from "./EventLogPanel";
import { formatDate, statusLabel } from "../lib/format";

function SidebarIcon({ name }) {
  if (name === "analysis") {
    return (
      <svg className="sidebar-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 4v16M4 12h16" />
      </svg>
    );
  }
  if (name === "telegram") {
    return (
      <svg className="sidebar-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="m3.5 11.2 16.4-6.3c.75-.28 1.42.45 1.14 1.2l-5.35 14.05c-.26.7-1.14.93-1.71.45l-3.45-2.87-1.72 1.65c-.4.38-1.06.1-1.06-.46v-3.2l8.25-7.3-10.1 5.9-2.47-1.79c-.53-.38-.49-1.1.07-1.33Z" />
      </svg>
    );
  }
  if (name === "upload") {
    return (
      <svg className="sidebar-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M6.5 3.25h7l4 4v13.5h-11a2 2 0 0 1-2-2V5.25a2 2 0 0 1 2-2Z" />
        <path d="M13.5 3.25v4h4M8 12h6M8 15.5h4" />
      </svg>
    );
  }
  if (name === "history") {
    return (
      <svg className="sidebar-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3L4.5 8.9" />
        <path d="M4.5 4.75V8.9h4.15M12 7.75v4.5l3 1.75" />
      </svg>
    );
  }
  if (name === "help") {
    return (
      <svg className="sidebar-icon" viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="8.75" />
        <path d="M9.75 9.25a2.4 2.4 0 0 1 4.65.85c0 1.9-2.4 2.05-2.4 3.8M12 17.2v.1" />
      </svg>
    );
  }
  return (
    <svg className="sidebar-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 5.75h14v10.5H9l-4 3z" />
      <path d="M8 9.25h8M8 12.75h5" />
    </svg>
  );
}

function jobTitle(job, chats) {
  if (job.source_type !== "telegram_chat") {
    return job.source_name ? `Uploaded: ${job.source_name}` : "Uploaded export";
  }
  const title = chats.find((chat) => chat.id === job.telegram_chat_id)?.title || "Telegram chat";
  return job.scheduled_report ? `Scheduled: ${title}` : title;
}

function jobSubtitle(job) {
  if (job.scheduled_report?.scheduled_for) {
    return `Scheduled for ${formatDate(job.scheduled_report.scheduled_for)}`;
  }
  return formatDate(job.created_at);
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
            <span className="analysis-source-icon" aria-hidden="true">
              <SidebarIcon name={job.source_type === "telegram_chat" ? "chat" : "upload"} />
            </span>
            <span className="analysis-list-copy">
              <strong>{jobTitle(job, chats)}</strong>
              <span>{jobSubtitle(job)}</span>
            </span>
            <span className={`status-dot status-dot-${job.status}`} aria-hidden="true" />
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
        <div className="sidebar-brand-identity">
          <span className="app-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <path d="M5 5.75h14v10.5H9l-4 3z" />
              <path d="M8 9.25h8M8 12.75h5" />
            </svg>
          </span>
          <div>
            <strong>Chat Analysis</strong>
            <span>Intelligence workspace</span>
          </div>
        </div>
        <button
          className={`sidebar-help${activeView === "tutorial" ? " is-active" : ""}`}
          type="button"
          aria-label="Open report tutorial"
          aria-describedby="report-tutorial-tooltip"
          aria-current={activeView === "tutorial" ? "page" : undefined}
          title="How to create a report"
          onClick={() => onNavigate("tutorial")}
        >
          <SidebarIcon name="help" />
          <span className="sidebar-help-tooltip" id="report-tutorial-tooltip" role="tooltip">Report tutorial</span>
        </button>
      </div>

      <nav className="primary-nav" aria-label="Main navigation">
        <button
          className={`nav-item${activeView === "analysis" ? " is-active" : ""}`}
          type="button"
          aria-current={activeView === "analysis" ? "page" : undefined}
          onClick={() => onNavigate("analysis")}
        >
          <span className="nav-icon"><SidebarIcon name="analysis" /></span>
          <span className="nav-copy"><strong>New Analysis</strong><small>Start from a chat or export</small></span>
        </button>
        <button
          className={`nav-item${activeView === "telegram" ? " is-active" : ""}`}
          type="button"
          aria-current={activeView === "telegram" ? "page" : undefined}
          onClick={() => onNavigate("telegram")}
        >
          <span className="nav-icon"><SidebarIcon name="telegram" /></span>
          <span className="nav-copy"><strong>Telegram Setup</strong><small>Sources, chats, and schedules</small></span>
        </button>
      </nav>

      <section className="sidebar-history">
        <div className="sidebar-section-heading">
          <span><SidebarIcon name="history" /> Recent analyses</span>
          <span className="sidebar-count">{jobs.length}</span>
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
