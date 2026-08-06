import { useMemo } from "react";
import { EXAMPLE_QUESTIONS } from "../lib/constants";

export function QuestionBuilder({ questions, setQuestions, status, setStatus }) {
  const questionRows = useMemo(
    () => questions.length ? questions : [{ id: "q1", text: "" }],
    [questions],
  );

  const syncQuestions = (nextQuestions) => {
    const normalized = nextQuestions.map((question, index) => ({ id: `q${index + 1}`, text: question.text }));
    setQuestions(normalized);
    const hasEmptyQuestion = normalized.some((question) => !question.text.trim());
    setStatus(hasEmptyQuestion
      ? { kind: "error", message: "Complete or remove empty questions before starting the analysis." }
      : { kind: "muted", message: "" });
  };

  const updateQuestion = (index, text) => {
    syncQuestions(questionRows.map((question, itemIndex) => (
      itemIndex === index ? { ...question, text } : question
    )));
  };

  const addQuestion = () => {
    syncQuestions([...questionRows, { id: `q${questionRows.length + 1}`, text: "" }]);
  };

  const removeQuestion = (index) => {
    const next = questionRows.filter((_, itemIndex) => itemIndex !== index);
    syncQuestions(next.length ? next : [{ id: "q1", text: "" }]);
  };

  return (
    <div className="question-editor">
      <div className="question-toolbar">
        <span>Questions</span>
        <div>
          <button className="text-button" type="button" onClick={() => syncQuestions(EXAMPLE_QUESTIONS)}>
            Use example
          </button>
          <button className="button button-secondary button-small" type="button" onClick={addQuestion}>
            Add question
          </button>
        </div>
      </div>

      <div className="question-fields">
        {questionRows.map((question, index) => (
          <div className="question-row" key={`${question.id}-${index}`}>
            <span className="question-index">{String(index + 1).padStart(2, "0")}</span>
            <textarea
              className="question-textarea"
              rows="2"
              placeholder="What narratives are being promoted in this chat?"
              value={question.text}
              aria-label={`Question ${index + 1}`}
              onChange={(event) => updateQuestion(index, event.target.value)}
            />
            <button
              className="remove-question"
              type="button"
              title="Remove question"
              aria-label={`Remove question ${index + 1}`}
              onClick={() => removeQuestion(index)}
            >
              ×
            </button>
          </div>
        ))}
      </div>
      {status.kind === "error" && <div className="inline-error">{status.message}</div>}
    </div>
  );
}
