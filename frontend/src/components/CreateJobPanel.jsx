import { useEffect, useMemo, useRef, useState } from "react";
import { DEFAULT_OPTIONS, DEFAULT_QUESTIONS } from "../lib/constants";
import { formatBytes } from "../lib/format";
import { QuestionBuilder } from "./QuestionBuilder";
import { QuestionSetsPanel } from "./QuestionSetsPanel";

function localDateTimeValue(date) {
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function chatSourceLabel(chat) {
  return chat.ingest_mode === "external_push" ? "External collector" : "Backend account";
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
  sourceMode,
  setSourceMode,
  telegramConnection,
  telegramChats,
  telegramChatId,
  setTelegramChatId,
  reportStart,
  setReportStart,
  reportEnd,
  setReportEnd,
  onOpenTelegram,
}) {
  const [dragging, setDragging] = useState(false);
  const [file, setFile] = useState(null);
  const inputRef = useRef(null);
  const backendTelegramConnected = Boolean(telegramConnection?.connected);
  const activeChats = useMemo(
    () => telegramChats.filter((chat) => chat.status !== "archived"),
    [telegramChats],
  );
  const usableChats = useMemo(
    () => activeChats.filter((chat) => chat.ingest_mode === "external_push" || backendTelegramConnected),
    [activeChats, backendTelegramConnected],
  );
  const unavailableBackendChats = useMemo(
    () => activeChats.filter((chat) => chat.ingest_mode !== "external_push" && !backendTelegramConnected),
    [activeChats, backendTelegramConnected],
  );
  const selectedChat = usableChats.find((chat) => chat.id === telegramChatId);
  const questionCount = questions.filter((question) => question.text.trim()).length;
  const questionsReady = questions.length > 0 && questions.every((question) => question.text.trim());
  const sourceReady = sourceMode === "upload"
    ? Boolean(file?.name.toLowerCase().endsWith(".zip"))
    : Boolean(selectedChat && reportStart && reportEnd);

  useEffect(() => {
    if (sourceMode !== "telegram_chat") return;
    if (usableChats.some((chat) => chat.id === telegramChatId)) return;
    setTelegramChatId(usableChats[0]?.id || "");
  }, [sourceMode, setTelegramChatId, telegramChatId, usableChats]);

  const sourceSummary = useMemo(() => {
    if (sourceMode === "upload") return file?.name || "No export selected";
    return selectedChat ? `${selectedChat.title} - ${chatSourceLabel(selectedChat)}` : "No Telegram chat selected";
  }, [file, selectedChat, sourceMode]);

  const fileText = file
    ? `${file.name} - ${formatBytes(file.size)}${file.name.toLowerCase().endsWith(".zip") ? "" : " - ZIP files only"}`
    : "Choose a Telegram Desktop ZIP export with JSON or HTML";

  const setOption = (key, value) => setOptions((current) => ({ ...current, [key]: value }));

  const reset = () => {
    setFile(null);
    if (inputRef.current) inputRef.current.value = "";
    setQuestions(DEFAULT_QUESTIONS);
    setOptions(DEFAULT_OPTIONS);
    setSelectedQuestionSetId(null);
    setQuestionStatus({ kind: "muted", message: "" });
  };

  const pickFile = (files) => setFile(files?.[0] || null);

  const start = () => onStartJob({
    file,
    sourceMode,
    telegramChatId,
    reportStart,
    reportEnd,
  });

  const onDrop = (event) => {
    event.preventDefault();
    setDragging(false);
    pickFile(event.dataTransfer?.files);
  };

  return (
    <section className="page analysis-page">
      <header className="page-header">
        <div>
          <span className="page-kicker">Workspace</span>
          <h1>New Analysis</h1>
          <p>Choose a source and define the questions the report should answer.</p>
        </div>
      </header>

      <div className="analysis-grid">
        <div className="analysis-column">
          <section className="surface analysis-section">
            <div className="section-heading">
              <div>
                <span className="section-index">01</span>
                <div>
                  <h2>Source</h2>
                  <p>Select an uploaded export or a collected Telegram chat.</p>
                </div>
              </div>
            </div>

            <div className="segmented-control" aria-label="Analysis source">
              <button
                className={sourceMode === "upload" ? "is-active" : ""}
                type="button"
                onClick={() => setSourceMode("upload")}
              >
                ZIP export
              </button>
              <button
                className={sourceMode === "telegram_chat" ? "is-active" : ""}
                type="button"
                onClick={() => setSourceMode("telegram_chat")}
              >
                Collected chat
              </button>
            </div>

            {sourceMode === "upload" ? (
              <label
                className={`upload-zone${dragging ? " is-dragging" : ""}${file ? " has-file" : ""}`}
                onDragEnter={(event) => {
                  event.preventDefault();
                  setDragging(true);
                }}
                onDragOver={(event) => event.preventDefault()}
                onDragLeave={() => setDragging(false)}
                onDrop={onDrop}
              >
                <input ref={inputRef} type="file" accept=".zip" onChange={(event) => pickFile(event.target.files)} />
                <span className="upload-file-type">ZIP</span>
                <span>
                  <strong>{file ? "Export selected" : "Select or drop an export"}</strong>
                  <small className={file && !file.name.toLowerCase().endsWith(".zip") ? "error-text" : ""}>{fileText}</small>
                </span>
              </label>
            ) : !usableChats.length ? (
              <div className="source-empty-state">
                <div>
                  <strong>No collected chats available</strong>
                  <p>
                    {unavailableBackendChats.length
                      ? "Only backend-account chats are configured, but no backend Telegram account is connected. Register the chat through the external collector or connect the backend account."
                      : "Register a chat through an external collector before creating a chat-based analysis."}
                  </p>
                </div>
                <button className="button button-secondary" type="button" onClick={onOpenTelegram}>
                  Open Telegram Setup
                </button>
              </div>
            ) : (
              <div className="telegram-source-fields">
                <label className="field field-wide">
                  <span>Group or channel</span>
                  <select value={telegramChatId} onChange={(event) => setTelegramChatId(event.target.value)}>
                    <option value="">Select a chat</option>
                    {usableChats.map((chat) => (
                      <option key={chat.id} value={chat.id}>
                        {chat.title} ({chatSourceLabel(chat)})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>From</span>
                  <input type="datetime-local" value={reportStart} onChange={(event) => setReportStart(event.target.value)} />
                </label>
                <label className="field">
                  <span>To</span>
                  <input type="datetime-local" value={reportEnd} onChange={(event) => setReportEnd(event.target.value)} />
                </label>
                <button
                  className="text-button date-preset"
                  type="button"
                  onClick={() => {
                    const end = new Date();
                    const start = new Date(end);
                    start.setDate(start.getDate() - 14);
                    setReportStart(localDateTimeValue(start));
                    setReportEnd(localDateTimeValue(end));
                  }}
                >
                  Use the last 14 days
                </button>
              </div>
            )}

            {sourceMode === "upload" && uploadInProgress && (
              <div className="upload-progress">
                <div>
                  <span>Uploading export</span>
                  <strong>{Math.round(uploadProgress)}%</strong>
                </div>
                <div className="progressbar"><div style={{ width: `${uploadProgress}%` }} /></div>
              </div>
            )}
          </section>

          <section className="surface analysis-section">
            <div className="section-heading">
              <div>
                <span className="section-index">02</span>
                <div>
                  <h2>Analysis options</h2>
                </div>
              </div>
            </div>

            <div className="option-list">
              <label className="option-row">
                <input
                  type="checkbox"
                  checked={options.analyze_media !== false}
                  onChange={(event) => setOption("analyze_media", event.target.checked)}
                />
                <span>Analyse media</span>
              </label>
              <label className="option-row">
                <input
                  type="checkbox"
                  checked={Boolean(options.translate)}
                  onChange={(event) => setOption("translate", event.target.checked)}
                />
                <span>Translate</span>
              </label>
            </div>
          </section>
        </div>

        <section className="surface analysis-section questions-section">
          <div className="section-heading questions-heading">
            <div>
              <span className="section-index">03</span>
              <div>
                <h2>Report questions</h2>
                <p>Each question becomes a separate section in the final report.</p>
              </div>
            </div>
            <span className="question-count">{questionCount} question{questionCount === 1 ? "" : "s"}</span>
          </div>

          <QuestionSetsPanel
            questionSets={questionSets}
            selectedId={selectedQuestionSetId}
            onSelect={onSelectQuestionSet}
            onSave={onSaveQuestionSet}
            onUpdate={onUpdateQuestionSet}
            onDelete={onDeleteQuestionSet}
          />
          <QuestionBuilder
            questions={questions}
            setQuestions={setQuestions}
            status={questionStatus}
            setStatus={setQuestionStatus}
          />
        </section>
      </div>

      <footer className="analysis-action-bar">
        <div className="analysis-summary">
          <span>Source</span>
          <strong>{sourceSummary}</strong>
          <span className="summary-divider" />
          <span>{questionCount} question{questionCount === 1 ? "" : "s"}</span>
        </div>
        <div className="analysis-actions">
          <button className="button button-ghost" type="button" onClick={reset} disabled={uploadInProgress}>
            Reset
          </button>
          <button
            className="button button-primary button-large"
            type="button"
            onClick={start}
            disabled={uploadInProgress || !sourceReady || !questionsReady}
          >
            Start analysis
          </button>
        </div>
      </footer>
    </section>
  );
}
