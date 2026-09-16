import { useEffect, useId, useMemo, useRef, useState } from "react";
import { DEFAULT_OPTIONS, DEFAULT_QUESTIONS } from "../lib/constants";
import { formatBytes } from "../lib/format";
import { QuestionBuilder } from "./QuestionBuilder";
import { QuestionSetsPanel } from "./QuestionSetsPanel";
import { WorkspaceRail, WorkspaceTopbar } from "./WorkspaceChrome";

function localDateTimeValue(date) {
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

const DEFAULT_TELEGRAM_REPORT_DAYS = 30;

function AnalysisIcon({ name }) {
  if (name === "upload") {
    return (
      <svg className="analysis-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M4.5 15.5v3a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-3" />
        <path d="M12 16V3.5M7.75 7.75 12 3.5l4.25 4.25" />
      </svg>
    );
  }

  if (name === "chat") {
    return (
      <svg className="analysis-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M5.5 5.75h13a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-7.1l-4.5 3v-3H5.5a2 2 0 0 1-2-2v-7.5a2 2 0 0 1 2-2Z" />
        <path d="M7.5 9.5h9M7.5 13.25h6" />
      </svg>
    );
  }

  if (name === "questions") {
    return (
      <svg className="analysis-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M5.5 4.5h13a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2Z" />
        <path d="M8 8.5h8M8 12h8M8 15.5h5" />
      </svg>
    );
  }

  if (name === "sparkles") {
    return (
      <svg className="analysis-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 2.75c.45 3.1 2.15 4.8 5.25 5.25-3.1.45-4.8 2.15-5.25 5.25C11.55 10.15 9.85 8.45 6.75 8 9.85 7.55 11.55 5.85 12 2.75Z" />
        <path d="M18 13.5c.28 2 1.5 3.22 3.5 3.5-2 .28-3.22 1.5-3.5 3.5-.28-2-1.5-3.22-3.5-3.5 2-.28 3.22-1.5 3.5-3.5ZM5.5 13c.2 1.35.95 2.1 2.3 2.3-1.35.2-2.1.95-2.3 2.3-.2-1.35-.95-2.1-2.3-2.3 1.35-.2 2.1-.95 2.3-2.3Z" />
      </svg>
    );
  }

  if (name === "file") {
    return (
      <svg className="analysis-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M6.5 2.75h7l4 4v14.5h-11a2 2 0 0 1-2-2V4.75a2 2 0 0 1 2-2Z" />
        <path d="M13.5 2.75v4h4M8 11h6M8 14.5h6M8 18h4" />
      </svg>
    );
  }

  return (
    <svg className="analysis-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M20.25 11.25v.75a8.25 8.25 0 1 1-4.9-7.55" />
      <path d="m8.25 11.75 2.5 2.5 8-8" />
    </svg>
  );
}

function InfoTooltip({ label, children }) {
  const tooltipId = useId();
  return (
    <span className="info-tooltip" tabIndex={0} aria-label={label} aria-describedby={tooltipId}>
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="8" cy="8" r="6.25" />
        <path d="M8 7.1v4M8 4.55v.1" />
      </svg>
      <span className="info-tooltip-content" id={tooltipId} role="tooltip">{children}</span>
    </span>
  );
}

function FieldLabel({ children, help }) {
  return (
    <span className="field-label-copy">
      {children}
      <InfoTooltip label={`About ${children}`}>{help}</InfoTooltip>
    </span>
  );
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
  const firstIncompleteQuestion = questions.findIndex((question) => !question.text.trim());
  const reportStartDate = new Date(reportStart);
  const reportEndDate = new Date(reportEnd);
  const reportDatesPresent = Boolean(reportStart && reportEnd);
  const reportDatesValid = reportDatesPresent
    && !Number.isNaN(reportStartDate.getTime())
    && !Number.isNaN(reportEndDate.getTime());
  const reportDateOrderValid = reportDatesValid && reportStartDate < reportEndDate;
  const reportRangeValid = reportDateOrderValid
    && reportEndDate.getTime() - reportStartDate.getTime() <= DEFAULT_TELEGRAM_REPORT_DAYS * 24 * 60 * 60 * 1000;
  const sourceReady = sourceMode === "upload"
    ? Boolean(file?.name.toLowerCase().endsWith(".zip"))
    : Boolean(selectedChat && reportRangeValid);
  const sourceIssue = sourceMode === "upload"
    ? !file ? "Choose a ZIP export" : !file.name.toLowerCase().endsWith(".zip") ? "Choose a file in ZIP format" : ""
    : !selectedChat
      ? "Choose a collected chat"
      : !reportDatesPresent
        ? "Set a reporting period"
        : !reportDateOrderValid
          ? "Start time must be before end time"
          : !reportRangeValid ? "Use a period of 30 days or less" : "";
  const questionIssue = !questions.length
    ? "Add a report question"
    : firstIncompleteQuestion >= 0 ? `Complete question ${firstIncompleteQuestion + 1}` : "";
  const readinessMessage = sourceIssue || questionIssue || "Ready to start analysis";
  const sourceInvalid = sourceMode === "upload"
    ? Boolean(file && !file.name.toLowerCase().endsWith(".zip"))
    : Boolean(selectedChat && reportDatesPresent && !reportRangeValid);
  const workspaceState = uploadInProgress
    ? "working"
    : sourceInvalid ? "attention" : sourceReady && questionsReady ? "ready" : "setup";
  const workspaceBadge = uploadInProgress
    ? "Uploading"
    : sourceInvalid ? "Check input" : sourceReady && questionsReady ? "Ready" : "Needs input";
  const processingLabel = options.analyze_media !== false && options.translate
    ? "Media + translation"
    : options.analyze_media !== false ? "Media analysis" : options.translate ? "Translation" : "Standard";

  useEffect(() => {
    if (sourceMode !== "telegram_chat") return;
    if (usableChats.some((chat) => chat.id === telegramChatId)) return;
    setTelegramChatId(usableChats[0]?.id || "");
  }, [sourceMode, setTelegramChatId, telegramChatId, usableChats]);

  const fileText = file
    ? `${formatBytes(file.size)}${file.name.toLowerCase().endsWith(".zip") ? " · Click to replace" : " · ZIP files only"}`
    : "Telegram Desktop export · JSON or HTML";

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
    <section className={`page workspace-page workspace-page-${workspaceState} analysis-page analysis-page-${workspaceState}`}>
      <WorkspaceTopbar
        tone={workspaceState}
        badge={workspaceBadge}
        title="New Analysis"
        subtitle={uploadInProgress ? `Uploading ${Math.round(uploadProgress)}%` : readinessMessage}
        actions={(
          <>
            <button className="button button-ghost" type="button" onClick={reset} disabled={uploadInProgress}>
              Reset
            </button>
            <button
              className="button button-primary button-large"
              type="button"
              onClick={start}
              disabled={uploadInProgress || !sourceReady || !questionsReady}
              title={!sourceReady || !questionsReady ? readinessMessage : undefined}
            >
              {uploadInProgress ? `Uploading ${Math.round(uploadProgress)}%` : "Start analysis"}
            </button>
          </>
        )}
      />

      <WorkspaceRail
        className="analysis-readiness-rail"
        ariaLabel="Analysis readiness"
        items={[
          {
            key: "evidence",
            label: "Evidence",
            status: sourceReady ? "completed" : sourceInvalid ? "failed" : "active",
            detail: sourceReady ? sourceMode === "upload" ? "ZIP selected" : "Chat selected" : sourceIssue,
            progress: sourceReady ? 100 : sourceInvalid ? 16 : 0,
          },
          {
            key: "questions",
            label: "Questions",
            status: questionsReady ? "completed" : "active",
            detail: questionsReady ? `${questionCount} ready` : questionIssue,
            progress: questionsReady ? 100 : 0,
          },
          {
            key: "processing",
            label: "Processing",
            status: "configured",
            detail: processingLabel,
            progress: 100,
          },
        ]}
      />

      <div className="analysis-grid">
        <div className="analysis-column">
          <section className="surface workspace-panel analysis-section analysis-source-card">
            <div className="analysis-card-heading">
              <div className="analysis-heading-copy">
                <span className="analysis-section-icon"><AnalysisIcon name="upload" /></span>
                <div>
                  <span className="analysis-section-kicker">Step 1</span>
                  <h2>
                    Choose evidence
                    <InfoTooltip label="About analysis sources">
                      Upload a Telegram Desktop ZIP once, or analyse messages kept current by a collector.
                    </InfoTooltip>
                  </h2>
                  <p>Select where the report should look for answers.</p>
                </div>
              </div>
            </div>

            <div className="analysis-source-switcher" aria-label="Analysis source">
              <button
                className={`analysis-source-choice${sourceMode === "upload" ? " is-active" : ""}`}
                type="button"
                aria-pressed={sourceMode === "upload"}
                onClick={() => setSourceMode("upload")}
              >
                <span className="source-choice-icon"><AnalysisIcon name="file" /></span>
                <span className="source-choice-copy">
                  <strong>ZIP export</strong>
                  <small>Upload a desktop export</small>
                </span>
                <span className="source-choice-check"><AnalysisIcon name="check" /></span>
              </button>
              <button
                className={`analysis-source-choice${sourceMode === "telegram_chat" ? " is-active" : ""}`}
                type="button"
                aria-pressed={sourceMode === "telegram_chat"}
                onClick={() => setSourceMode("telegram_chat")}
              >
                <span className="source-choice-icon"><AnalysisIcon name="chat" /></span>
                <span className="source-choice-copy">
                  <strong>Collected chat</strong>
                  <small>Use synchronized messages</small>
                </span>
                <span className="source-choice-check"><AnalysisIcon name="check" /></span>
              </button>
            </div>

            {sourceMode === "upload" ? (
              <label
                className={`upload-zone analysis-upload-zone${dragging ? " is-dragging" : ""}${file ? " has-file" : ""}${file && !file.name.toLowerCase().endsWith(".zip") ? " has-error" : ""}`}
                onDragEnter={(event) => {
                  event.preventDefault();
                  setDragging(true);
                }}
                onDragOver={(event) => event.preventDefault()}
                onDragLeave={() => setDragging(false)}
                onDrop={onDrop}
              >
                <input ref={inputRef} type="file" accept=".zip" onChange={(event) => pickFile(event.target.files)} />
                <span className="analysis-upload-icon"><AnalysisIcon name={file ? "file" : "upload"} /></span>
                <span className="upload-zone-copy">
                  <span className={`upload-state-label${file?.name.toLowerCase().endsWith(".zip") ? " is-ready" : ""}`}>
                    {file?.name.toLowerCase().endsWith(".zip") ? "Ready" : file ? "ZIP required" : "Telegram export"}
                  </span>
                  <strong>{file ? file.name : "Drop a ZIP export here"}</strong>
                  <small className={file && !file.name.toLowerCase().endsWith(".zip") ? "error-text" : ""}>{fileText}</small>
                </span>
                <span className="upload-browse-label">Browse</span>
              </label>
            ) : !usableChats.length ? (
              <div className="source-empty-state analysis-source-empty">
                <span className="analysis-empty-icon"><AnalysisIcon name="chat" /></span>
                <div>
                  <strong>No collected chats available</strong>
                  <p>
                    {unavailableBackendChats.length
                      ? `${unavailableBackendChats.length} Backend account chat${unavailableBackendChats.length === 1 ? " is" : "s are"} unavailable until Telegram reconnects.`
                      : "Set up an External collector or connect Telegram first."}
                  </p>
                </div>
                <button className="button button-secondary" type="button" onClick={onOpenTelegram}>
                  Open Telegram Setup
                </button>
              </div>
            ) : (
              <div className="telegram-source-fields analysis-telegram-source">
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
                  <FieldLabel help="The report includes messages from this local date and time.">From</FieldLabel>
                  <input type="datetime-local" value={reportStart} onChange={(event) => setReportStart(event.target.value)} />
                </label>
                <label className="field">
                  <FieldLabel help="The reporting period must end after it starts and can cover at most 30 days.">To</FieldLabel>
                  <input type="datetime-local" value={reportEnd} onChange={(event) => setReportEnd(event.target.value)} />
                </label>
                {selectedChat && reportDatesPresent && !reportRangeValid && (
                  <div className="analysis-source-error">
                    {reportDateOrderValid ? "Reports can cover at most 30 days." : "The start time must be before the end time."}
                  </div>
                )}
                <button
                  className="text-button date-preset analysis-date-preset"
                  type="button"
                  onClick={() => {
                    const end = new Date();
                    const start = new Date(end);
                    start.setTime(start.getTime() - DEFAULT_TELEGRAM_REPORT_DAYS * 24 * 60 * 60 * 1000);
                    setReportStart(localDateTimeValue(start));
                    setReportEnd(localDateTimeValue(end));
                  }}
                >
                  Use the last 30 days
                </button>
                <label className="analysis-inline-option field-wide">
                  <input
                    type="checkbox"
                    checked={Boolean(options.allow_partial_telegram_sync)}
                    onChange={(event) => setOption("allow_partial_telegram_sync", event.target.checked)}
                  />
                  <span>
                    <span className="field-label-copy">
                      Allow partial report
                      <InfoTooltip label="About partial reports">
                        Start with stored messages instead of waiting for collection to catch up. Recent messages may be missing.
                      </InfoTooltip>
                    </span>
                    <small>Use currently stored messages</small>
                  </span>
                </label>
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

          <details className="surface workspace-panel analysis-section analysis-options-card">
            <summary className="analysis-options-summary">
              <div className="analysis-heading-copy">
                <span className="analysis-section-icon"><AnalysisIcon name="sparkles" /></span>
                <div>
                  <span className="analysis-section-kicker">Optional</span>
                  <h2>Processing enhancements</h2>
                </div>
              </div>
              <div className="analysis-option-summary-chips">
                <span className={options.analyze_media !== false ? "is-on" : ""}>Media {options.analyze_media !== false ? "on" : "off"}</span>
                <span className={options.translate ? "is-on" : ""}>Translation {options.translate ? "on" : "off"}</span>
                <span className="analysis-details-chevron">⌄</span>
              </div>
            </summary>

            <div className="option-list analysis-option-list">
              <label className={`analysis-option-card${options.analyze_media !== false ? " is-active" : ""}`}>
                <span className="analysis-option-icon"><AnalysisIcon name="sparkles" /></span>
                <span className="analysis-option-copy">
                  <span className="field-label-copy">
                    Analyse media
                    <InfoTooltip label="About media analysis">
                      Describe images and process supported audio or video. This can make the analysis take longer.
                    </InfoTooltip>
                  </span>
                  <small>Images, audio and video</small>
                </span>
                <input
                  className="analysis-switch"
                  type="checkbox"
                  checked={options.analyze_media !== false}
                  onChange={(event) => setOption("analyze_media", event.target.checked)}
                />
              </label>
              <label className={`analysis-option-card${options.translate ? " is-active" : ""}`}>
                <span className="analysis-option-icon"><AnalysisIcon name="questions" /></span>
                <span className="analysis-option-copy">
                  <span className="field-label-copy">
                    Translate evidence
                    <InfoTooltip label="About evidence translation">
                      Translate source evidence to English before retrieval while preserving the original stored content.
                    </InfoTooltip>
                  </span>
                  <small>Use English for report evidence</small>
                </span>
                <input
                  className="analysis-switch"
                  type="checkbox"
                  checked={Boolean(options.translate)}
                  onChange={(event) => setOption("translate", event.target.checked)}
                />
              </label>
            </div>
          </details>
        </div>

        <section className="surface workspace-panel analysis-section questions-section analysis-questions-card">
          <div className="analysis-card-heading questions-heading">
            <div className="analysis-heading-copy">
              <span className="analysis-section-icon"><AnalysisIcon name="questions" /></span>
              <div>
                <span className="analysis-section-kicker">Step 2</span>
                <h2>
                  Define the report
                  <InfoTooltip label="About report questions">
                    Each question becomes a focused report section. Saved question sets can be reused in schedules.
                  </InfoTooltip>
                </h2>
                <p>Ask only what the finished report should answer.</p>
              </div>
            </div>
            <span className={`question-count${questionsReady ? " is-ready" : ""}`}>{questionCount} question{questionCount === 1 ? "" : "s"}</span>
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

    </section>
  );
}
