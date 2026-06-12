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

  return (
    <details className="diagnostics">
      <summary>
        <span className={`utility-status${acceptingJobs ? "" : " is-warning"}`} aria-hidden="true" />
        <span>Diagnostics</span>
      </summary>

      <div className="diagnostics-body">
        <div className="diagnostics-capacity">
          <div>
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
