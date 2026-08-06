import { useState } from "react";

export function QuestionSetsPanel({
  questionSets,
  selectedId,
  onSelect,
  onSave,
  onUpdate,
  onDelete,
}) {
  const selected = questionSets.find((item) => item.id === selectedId);
  const [editorMode, setEditorMode] = useState(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);

  const openEditor = (mode) => {
    setEditorMode(mode);
    setConfirmDelete(false);
    setName(mode === "update" ? selected?.name || "" : "");
    setDescription(mode === "update" ? selected?.description || "" : "");
  };

  const closeEditor = () => {
    setEditorMode(null);
    setName("");
    setDescription("");
  };

  const submit = async (event) => {
    event.preventDefault();
    const cleanName = name.trim();
    if (!cleanName) return;
    setBusy(true);
    try {
      if (editorMode === "update") {
        await onUpdate({ name: cleanName, description: description.trim() || null });
      } else {
        await onSave({ name: cleanName, description: description.trim() || null });
      }
      closeEditor();
    } catch {
      // The parent presents the API error as a toast.
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await onDelete();
      setConfirmDelete(false);
      closeEditor();
    } catch {
      // The parent presents the API error as a toast.
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="question-set-control">
      <div className="question-set-toolbar">
        <div className="question-set-picker">
          <label className="field">
            <span>Question set</span>
            <select value={selectedId || ""} onChange={(event) => {
              closeEditor();
              setConfirmDelete(false);
              onSelect(event.target.value || null);
            }}>
              <option value="">Custom questions</option>
              {questionSets.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} ({item.question_count || item.questions?.length || 0})
                </option>
              ))}
            </select>
          </label>
          <span className={`question-set-state${selected ? " is-saved" : ""}`}>
            <span className="status-dot" /> {selected ? "Saved set" : "Unsaved custom set"}
          </span>
        </div>
        <div className="question-set-actions">
          <button className="button button-ghost button-small" type="button" onClick={() => openEditor("create")}>
            Save as new
          </button>
          {selected && (
            <>
              <button className="button button-ghost button-small" type="button" onClick={() => openEditor("update")}>
                Edit
              </button>
              <button className="button button-ghost button-small danger-text" type="button" onClick={() => {
                closeEditor();
                setConfirmDelete(true);
              }}>
                Delete
              </button>
            </>
          )}
        </div>
      </div>

      {selected?.description && !editorMode && !confirmDelete && (
        <p className="question-set-description">{selected.description}</p>
      )}

      {editorMode && (
        <form className="inline-template-form" onSubmit={submit}>
          <div className="inline-template-heading">
            <span className="inline-template-icon" aria-hidden="true">{editorMode === "update" ? "✎" : "+"}</span>
            <div><strong>{editorMode === "update" ? "Edit question set" : "Save question set"}</strong><small>Reuse these questions in future analyses.</small></div>
          </div>
          <label className="field">
            <span>Name</span>
            <input value={name} maxLength={200} autoFocus onChange={(event) => setName(event.target.value)} />
          </label>
          <label className="field field-wide">
            <span>Description <em>optional</em></span>
            <input value={description} maxLength={2000} onChange={(event) => setDescription(event.target.value)} />
          </label>
          <div className="inline-form-actions">
            <button className="button button-ghost button-small" type="button" onClick={closeEditor} disabled={busy}>
              Cancel
            </button>
            <button className="button button-primary button-small" type="submit" disabled={busy || !name.trim()}>
              {editorMode === "update" ? "Update set" : "Save set"}
            </button>
          </div>
        </form>
      )}

      {confirmDelete && selected && (
        <div className="inline-confirmation">
          <span className="confirmation-icon" aria-hidden="true">!</span>
          <span className="confirmation-copy">Delete <strong>{selected.name}</strong>? <small>Existing analyses will not be affected.</small></span>
          <div>
            <button className="button button-ghost button-small" type="button" onClick={() => setConfirmDelete(false)} disabled={busy}>
              Cancel
            </button>
            <button className="button button-danger button-small" type="button" onClick={remove} disabled={busy}>
              Delete set
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
