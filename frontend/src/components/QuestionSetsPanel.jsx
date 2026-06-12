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
        <div className="question-set-actions">
          <button className="text-button" type="button" onClick={() => openEditor("create")}>
            Save as new
          </button>
          {selected && (
            <>
              <button className="text-button" type="button" onClick={() => openEditor("update")}>
                Edit details
              </button>
              <button className="text-button danger-text" type="button" onClick={() => {
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
          <span>Delete <strong>{selected.name}</strong>? Existing analyses will not be affected.</span>
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
