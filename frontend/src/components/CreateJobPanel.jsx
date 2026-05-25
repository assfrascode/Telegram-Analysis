import { useRef, useState } from "react";
import { DEFAULT_OPTIONS, DEFAULT_QUESTIONS } from "../lib/constants";
import { formatBytes } from "../lib/format";
import { QuestionBuilder } from "./QuestionBuilder";
import { QuestionSetsPanel } from "./QuestionSetsPanel";

function WorkflowStep({ number, title, description, children }) {
  return (
    <section className="workflow-step">
      <div className="step-header">
        <span className="step-number">{number}</span>
        <div>
          <h3>{title}</h3>
          {description && <p>{description}</p>}
        </div>
      </div>
      <div className="step-body">{children}</div>
    </section>
  );
}

export function CreateJobPanel({
  questions,
  setQuestions,
  options,
  setOptions,
  questionStatus,
  setQuestionStatus,
  questionSets,
  selectedQuestionSetId,
  setSelectedQuestionSetId,
  uploadProgress,
  uploadInProgress,
  onStartJob,
  onSelectQuestionSet,
  onSaveQuestionSet,
  onUpdateQuestionSet,
  onDeleteQuestionSet,
}) {
  const [dragging, setDragging] = useState(false);
  const [file, setFile] = useState(null);
  const inputRef = useRef(null);

  const fileText = file
    ? `${file.name} · ${formatBytes(file.size)}${file.name.toLowerCase().endsWith(".zip") ? "" : " · Bitte ZIP-Datei auswählen"}`
    : "Noch keine Datei ausgewählt.";

  const setOption = (key, value) => setOptions((current) => ({ ...current, [key]: value }));

  const reset = () => {
    setFile(null);
    if (inputRef.current) inputRef.current.value = "";
    setQuestions(DEFAULT_QUESTIONS);
    setOptions(DEFAULT_OPTIONS);
    setSelectedQuestionSetId(null);
    setQuestionStatus({ kind: "muted", message: "Tragen Sie mindestens eine Frage ein." });
  };

  const pickFile = (files) => {
    const next = files?.[0] || null;
    setFile(next);
  };

  const start = () => onStartJob(file);

  const onDrop = (event) => {
    event.preventDefault();
    setDragging(false);
    pickFile(event.dataTransfer?.files);
  };

  return (
    <section className="panel content-panel create-panel-guided">
      <div className="panel-header guided-intro">
        <span className="section-kicker">Neue Analyse</span>
        <h2>Telegram-Export auswerten</h2>
        <p>Folgen Sie den vier Schritten. Die Analyse startet erst, wenn eine ZIP-Datei ausgewählt und mindestens eine Frage eingetragen ist.</p>
      </div>

      <div className="workflow-list">
        <WorkflowStep number="1" title="Export hochladen" description="Wählen Sie den ZIP-Export aus Telegram aus.">
          <label
            className={`upload-zone${dragging ? " is-dragging" : ""}`}
            onDragEnter={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            <input ref={inputRef} type="file" accept=".zip" onChange={(event) => pickFile(event.target.files)} />
            <div className="upload-icon">⇧</div>
            <div>
              <strong>ZIP-Datei auswählen oder hier ablegen</strong>
              <p className="hint compact-hint" style={{ color: file && !file.name.toLowerCase().endsWith(".zip") ? "var(--red)" : undefined }}>
                {fileText}
              </p>
            </div>
          </label>

          {uploadInProgress && (
            <div className="progress-row">
              <div className="progress-label">
                <span>Datei wird hochgeladen</span>
                <span>{Math.round(uploadProgress)}%</span>
              </div>
              <div className="progressbar">
                <div style={{ width: `${uploadProgress}%` }} />
              </div>
            </div>
          )}
        </WorkflowStep>

        <WorkflowStep number="2" title="Analyseoptionen wählen" description="Die empfohlenen Einstellungen sind bereits vorausgewählt.">
          <div className="options-strip user-options-strip">
            <label className="switch option-card">
              <input
                type="checkbox"
                checked={options.analyze_media !== false}
                onChange={(event) => setOption("analyze_media", event.target.checked)}
              />
              <span>
                <strong>Bilder und Videos auswerten</strong>
                <small>Empfohlen, wenn der Export Medien enthält.</small>
              </span>
            </label>
            <label className="switch option-card">
              <input type="checkbox" checked={Boolean(options.translate)} onChange={(event) => setOption("translate", event.target.checked)} />
              <span>
                <strong>Fremdsprachige Inhalte übersetzen</strong>
                <small>Optional für Chats mit mehreren Sprachen.</small>
              </span>
            </label>
          </div>
        </WorkflowStep>

        <WorkflowStep number="3" title="Fragen festlegen" description="Sie können ein gespeichertes Fragenset verwenden oder eigene Fragen eintragen.">
          <QuestionSetsPanel
            questionSets={questionSets}
            selectedId={selectedQuestionSetId}
            onSelect={onSelectQuestionSet}
            onSave={onSaveQuestionSet}
            onUpdate={onUpdateQuestionSet}
            onDelete={onDeleteQuestionSet}
          />

          <QuestionBuilder questions={questions} setQuestions={setQuestions} status={questionStatus} setStatus={setQuestionStatus} />
        </WorkflowStep>

        <WorkflowStep number="4" title="Analyse starten" description="Nach dem Start sehen Sie automatisch den Fortschritt und können den Bericht herunterladen, sobald er fertig ist.">
          <div className="actions-row start-actions">
            <button className="button button-primary button-large" type="button" onClick={start} disabled={uploadInProgress}>
              Analyse starten
            </button>
            <button className="button button-secondary" type="button" onClick={reset} disabled={uploadInProgress}>
              Eingaben zurücksetzen
            </button>
          </div>
        </WorkflowStep>
      </div>
    </section>
  );
}
