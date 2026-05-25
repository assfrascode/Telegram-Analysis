function levelLabel(level) {
  if (level === "error") return "Fehler";
  if (level === "warning") return "Warnung";
  return "Information";
}

export function EventLogPanel({ events, filter, setFilter, onClear }) {
  const filtered = filter === "all" ? events : events.filter((event) => event.level === filter);

  return (
    <aside className="technical-panel panel">
      <details>
        <summary>
          <div>
            <span className="section-kicker">Optional</span>
            <h2>Technische Details</h2>
            <p className="hint compact-hint">Diese Informationen sind nur bei Fehlersuche nötig.</p>
          </div>
        </summary>
        <div className="technical-actions">
          <label className="field inline-field technical-filter">
            <span>Einträge anzeigen</span>
            <select className="select-small" value={filter} onChange={(event) => setFilter(event.target.value)}>
              <option value="all">Alle</option>
              <option value="error">Fehler</option>
              <option value="warning">Warnungen</option>
              <option value="info">Informationen</option>
            </select>
          </label>
          <button className="button button-secondary button-small" type="button" onClick={onClear}>
            Protokoll leeren
          </button>
        </div>
        <div className="event-log" aria-live="polite">
          {filtered.length ? (
            filtered.map((event, index) => {
              const timestamp = new Date(event.created_at).toLocaleTimeString("de-DE");
              return (
                <div className="event-row event-row-simple" key={event.id || `${event.created_at}-${index}`}>
                  <span className="event-time">{timestamp}</span>
                  <span className="event-level">{levelLabel(event.level)}</span>
                  <span className="event-message">{event.message || event.event_type}</span>
                </div>
              );
            })
          ) : (
            <div className="soft-empty-state">Keine technischen Einträge sichtbar.</div>
          )}
        </div>
      </details>
    </aside>
  );
}
