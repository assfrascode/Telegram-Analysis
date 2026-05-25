import { useMemo } from "react";
import { EXAMPLE_QUESTIONS } from "../lib/constants";
import { normalizeQuestions } from "../lib/format";

export function QuestionBuilder({ questions, setQuestions, status, setStatus }) {
  const questionRows = useMemo(() => questions.length ? questions : [{ id: "q1", text: "" }], [questions]);

  const syncQuestions = (nextQuestions) => {
    const normalized = nextQuestions.map((question, index) => ({ id: `q${index + 1}`, text: question.text }));
    setQuestions(normalized);
    setStatus({ kind: "muted", message: "Tragen Sie mindestens eine Frage ein." });
  };

  const updateQuestion = (index, text) => {
    const next = questionRows.map((question, itemIndex) => (itemIndex === index ? { ...question, text } : question));
    syncQuestions(next);
  };

  const addQuestion = () => {
    syncQuestions([...questionRows, { id: `q${questionRows.length + 1}`, text: "" }]);
  };

  const removeQuestion = (index) => {
    const next = questionRows.filter((_, itemIndex) => itemIndex !== index);
    syncQuestions(next.length ? next : [{ id: "q1", text: "" }]);
  };

  const validateFields = () => {
    try {
      const normalized = normalizeQuestions(questionRows);
      setQuestions(normalized);
      setStatus({ kind: "success", message: `${normalized.length} Frage(n) bereit.` });
      return normalized;
    } catch (error) {
      setStatus({ kind: "error", message: error.message });
      return null;
    }
  };

  const color = status.kind === "success" ? "var(--green)" : status.kind === "error" ? "var(--red)" : undefined;

  return (
    <div className="questions-builder-card simplified-card">
      <div className="card-heading-row">
        <div>
          <strong>Fragen für den Bericht</strong>
          <p className="hint compact-hint">Jede Frage wird einzeln beantwortet. Kurze, konkrete Fragen liefern meist die besten Ergebnisse.</p>
        </div>
        <div className="inline-actions question-actions">
          <button className="button button-secondary button-small" type="button" onClick={addQuestion}>
            Frage hinzufügen
          </button>
          <button className="button button-secondary button-small" type="button" onClick={() => syncQuestions(EXAMPLE_QUESTIONS)}>
            Beispiel einsetzen
          </button>
          <button className="button button-secondary button-small" type="button" onClick={validateFields}>
            Fragen prüfen
          </button>
        </div>
      </div>

      <div className="question-fields">
        {questionRows.map((question, index) => (
          <div className="question-row" key={`${question.id}-${index}`}>
            <div className="question-index">{index + 1}</div>
            <textarea
              className="question-textarea"
              rows="2"
              placeholder="Zum Beispiel: Welche Narrative verbreitet der Chat?"
              value={question.text}
              onChange={(event) => updateQuestion(index, event.target.value)}
            />
            <button className="button button-icon question-remove" type="button" title="Frage entfernen" onClick={() => removeQuestion(index)}>
              ×
            </button>
          </div>
        ))}
      </div>
      <div className="hint status-hint" style={{ color }}>{status.message}</div>
    </div>
  );
}
