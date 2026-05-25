export function QuestionSetsPanel({
  questionSets,
  selectedId,
  onSelect,
  onSave,
  onUpdate,
  onDelete,
}) {
  const selected = questionSets.find((item) => item.id === selectedId);

  return (
    <div className="question-set-card simplified-card">
      <div className="card-heading-row">
        <div>
          <strong>Fragenset</strong>
          <p className="hint compact-hint">Fragensets speichern wiederkehrende Fragen, damit sie nicht jedes Mal neu eingegeben werden müssen.</p>
        </div>
        <button className="button button-secondary button-small" type="button" onClick={onSave}>
          Dieses Fragenset speichern
        </button>
      </div>

      <div className="question-set-flow">
        <label className="field inline-field">
          <span>Fragenset auswählen</span>
          <select value={selectedId || ""} onChange={(event) => onSelect(event.target.value || null)}>
            <option value="">Ohne gespeichertes Fragenset</option>
            {questionSets.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} · {item.question_count || item.questions?.length || 0} Frage(n)
              </option>
            ))}
          </select>
        </label>

        {selected ? (
          <div className="template-actions-card">
            <div>
              <span className="meta-label">Aktuelles Fragenset</span>
              <strong>{selected.name}</strong>
              {selected.description && <p className="hint compact-hint">{selected.description}</p>}
              <p className="hint compact-hint">Änderungen an den Fragen gelten für diese Analyse sofort. Speichern Sie nur, wenn das Fragenset dauerhaft geändert werden soll.</p>
            </div>
            <div className="template-actions">
              <button className="button button-secondary button-small" type="button" onClick={onUpdate}>
                Änderungen speichern
              </button>
              <button className="button button-danger button-small" type="button" onClick={onDelete}>
                Fragenset löschen
              </button>
            </div>
          </div>
        ) : (
          <div className="soft-empty-state">Kein Fragenset ausgewählt. Die unten eingetragenen Fragen werden nur für diese Analyse verwendet.</div>
        )}
      </div>

      {!questionSets.length && <div className="hint">Noch keine Fragensets gespeichert.</div>}
    </div>
  );
}
