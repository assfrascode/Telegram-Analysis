function levelLabel(level) {
  if (level === "error") return "Error";
  if (level === "warning") return "Warning";
  return "Info";
}

export function EventLogPanel({
  events,
  filter,
  setFilter,
  onClear,
  capacity,
  onRefreshCapacity,
}) {
  const filtered = filter === "all" ? events : events.filter((event) => event.level === filter);
  const acceptingJobs = capacity?.accepting_jobs !== false;
  const issueCount = events.filter((event) => event.level === "error" || event.level === "warning").length;

  return (
    <details className="diagnostics">
      <summary>
        <span className={`utility-status${acceptingJobs ? "" : " is-warning"}`} aria-hidden="true" />
        <span className="diagnostics-summary-copy">
          <strong>Diagnostics</strong>
          <small>{acceptingJobs ? "System ready" : "Attention needed"}</small>
        </span>
        {issueCount > 0 && <span className="diagnostics-issue-count">{issueCount}</span>}
        <svg className="diagnostics-chevron" viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4" /></svg>
      </summary>

      <div className="diagnostics-body">
        <div className="diagnostics-capacity">
          <span className={`diagnostics-capacity-icon${acceptingJobs ? "" : " is-warning"}`} aria-hidden="true">
            <svg viewBox="0 0 24 24"><path d="M3 12h4l2.1-5.25 4.1 10.5L15.5 12H21" /></svg>
          </span>
          <div className="diagnostics-capacity-copy">
            <strong>{acceptingJobs ? "System ready" : "New analyses blocked"}</strong>
            <span>
              {capacity
                ? `${capacity.counts?.active_jobs ?? "?"}/${capacity.limits?.max_active_jobs ?? "?"} active analyses`
                : "Capacity has not been checked"}
            </span>
          </div>
          <button className="text-button" type="button" onClick={onRefreshCapacity}>
            Refresh
          </button>
        </div>

        <div className="diagnostics-toolbar">
          <select value={filter} onChange={(event) => setFilter(event.target.value)} aria-label="Filter diagnostic events">
            <option value="all">All events</option>
            <option value="error">Errors</option>
            <option value="warning">Warnings</option>
            <option value="info">Information</option>
          </select>
          <button className="text-button" type="button" onClick={onClear}>
            Clear
          </button>
        </div>

        <div className="event-log" aria-live="polite">
          {filtered.length ? (
            filtered.map((event, index) => (
              <div className="event-row" key={event.id || `${event.created_at}-${index}`}>
                <span className={`event-dot event-dot-${event.level}`} aria-hidden="true" />
                <span className="event-time">{new Date(event.created_at).toLocaleTimeString("en-GB")}</span>
                <span className={`event-level event-level-${event.level}`}>{levelLabel(event.level)}</span>
                <span className="event-message">{event.message || event.event_type}</span>
              </div>
            ))
          ) : (
            <div className="diagnostics-empty">No diagnostic events.</div>
          )}
        </div>
      </div>
    </details>
  );
}
