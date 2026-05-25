function ResourceCard({ name, value = {} }) {
  const ok = value.ok !== false;
  const status = value.status || (ok ? "ok" : "error");
  const extra = Object.entries(value)
    .filter(([key]) => !["ok", "status"].includes(key))
    .slice(0, 3);

  return (
    <div className="resource-card">
      <strong>{name}</strong>
      <span className={ok ? "badge badge-success" : "badge badge-error"}>{status}</span>
      {extra.map(([key, item]) => (
        <div className="hint" key={key}>
          {key}: {typeof item === "object" ? JSON.stringify(item) : String(item)}
        </div>
      ))}
    </div>
  );
}

function flattenResources(resources = {}) {
  const cards = [];
  for (const [name, value] of Object.entries(resources)) {
    if (name === "vllm" && value && typeof value === "object") {
      for (const [subName, subValue] of Object.entries(value)) {
        cards.push([`vLLM ${subName}`, subValue]);
      }
    } else {
      cards.push([name, value]);
    }
  }
  return cards;
}

export function CapacityModal({ open, capacity, onClose, onRefresh }) {
  if (!open) return null;

  const blockers = capacity?.blockers?.length ? capacity.blockers : [];
  const resourceCards = flattenResources(capacity?.resources || {});

  return (
    <div className="modal-backdrop" onClick={(event) => event.target === event.currentTarget && onClose()}>
      <section className="modal panel">
        <div className="panel-header split">
          <div>
            <span className="section-kicker">System</span>
            <h2>Kapazität</h2>
            <p>Aktuelle Aufnahmebereitschaft und Ressourcenzustand.</p>
          </div>
          <div className="inline-actions">
            <button className="button button-secondary button-small" type="button" onClick={onRefresh}>
              Aktualisieren
            </button>
            <button className="button button-icon" type="button" onClick={onClose} title="Schließen">
              ×
            </button>
          </div>
        </div>

        {!capacity ? (
          <div className="capacity-summary muted">Noch keine Daten.</div>
        ) : (
          <>
            <div className={`capacity-summary ${capacity.accepting_jobs ? "" : "alert-warning"}`}>
              <strong>{capacity.accepting_jobs ? "Jobs werden angenommen" : "Job-Annahme blockiert"}</strong>
              <br />
              Blocker: {blockers.length ? blockers.join(", ") : "keine"}
              <br />
              Aktive Jobs: {capacity.counts?.active_jobs ?? "?"}/{capacity.limits?.max_active_jobs ?? "?"}
              <br />
              Pending Worker Tasks: {capacity.counts?.pending_worker_tasks ?? "?"}/{capacity.limits?.max_pending_worker_tasks ?? "?"}
              <br />
              Pending Media: {capacity.counts?.pending_media_tasks ?? "?"}/{capacity.limits?.max_pending_media_tasks ?? "?"}
            </div>
            <div className="resource-grid">
              {resourceCards.length ? (
                resourceCards.map(([name, value]) => <ResourceCard key={name} name={name} value={value} />)
              ) : (
                <div className="muted">Keine Ressourceninformationen.</div>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  );
}
