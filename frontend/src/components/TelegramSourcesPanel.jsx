import { useEffect, useId, useState } from "react";
import { formatDate } from "../lib/format";
import { WorkspaceRail, WorkspaceTopbar } from "./WorkspaceChrome";

function TelegramIcon({ name }) {
  if (name === "chat") {
    return (
      <svg className="telegram-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M5.5 5.75h13a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-7.1l-4.5 3v-3H5.5a2 2 0 0 1-2-2v-7.5a2 2 0 0 1 2-2Z" />
        <path d="M7.5 9.5h9M7.5 13.25h6" />
      </svg>
    );
  }

  if (name === "calendar") {
    return (
      <svg className="telegram-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M5.5 4.75h13a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-12a2 2 0 0 1 2-2Z" />
        <path d="M7.5 2.75v4M16.5 2.75v4M3.5 9h17M7.5 13h3M13.5 13h3M7.5 16.5h3" />
      </svg>
    );
  }

  if (name === "shield") {
    return (
      <svg className="telegram-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 2.75 20 6v5.5c0 4.7-3.1 8.1-8 9.75-4.9-1.65-8-5.05-8-9.75V6l8-3.25Z" />
        <path d="m8.5 12 2.25 2.25 4.75-5" />
      </svg>
    );
  }

  if (name === "pulse") {
    return (
      <svg className="telegram-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M3 12h4l2.1-5.25 4.1 10.5L15.5 12H21" />
      </svg>
    );
  }

  return (
    <svg className="telegram-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="m3.5 11.2 16.4-6.3c.75-.28 1.42.45 1.14 1.2l-5.35 14.05c-.26.7-1.14.93-1.71.45l-3.45-2.87-1.72 1.65c-.4.38-1.06.1-1.06-.46v-3.2l8.25-7.3-10.1 5.9-2.47-1.79c-.53-.38-.49-1.1.07-1.33Z" />
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

function FieldLabel({ children, help, helpLabel = `About ${children}` }) {
  return (
    <span className="field-label-copy">
      {children}
      {help && <InfoTooltip label={helpLabel}>{help}</InfoTooltip>}
    </span>
  );
}

function chatStatusText(status) {
  if (status === "syncing") return "Syncing";
  if (status === "error") return "Sync failed";
  if (status === "archived") return "Archived";
  return "Active";
}

const REPORT_TIMEZONE = "Europe/Berlin";

function rollingWindowLabel(days) {
  return Number(days) === 1 ? "1 day" : `${days} days`;
}

function syncIntervalValue(chat) {
  return String(chat.sync_interval_minutes ?? 60);
}

function nextSyncLabel(chat) {
  if (chat.status === "syncing" || chat.status === "archived") return "-";
  if (chat.sync_interval_minutes === 0) return "Manual only";
  if (
    chat.ingest_mode === "external_push"
    && !chat.last_sync_at
    && new Date(chat.next_sync_at).getUTCFullYear() === 9999
  ) {
    return "Awaiting first sync";
  }
  return formatDate(chat.next_sync_at);
}

function scheduleIntervalLabel(days) {
  const window = rollingWindowLabel(days);
  return `Every ${window} · previous ${window}`;
}

function chatSourceLabel(chat) {
  return chat.ingest_mode === "external_push" ? "External collector" : "Backend account";
}

function SourceBadge({ chat }) {
  const external = chat.ingest_mode === "external_push";
  return (
    <span
      className={`source-badge source-badge-${external ? "external" : "backend"}`}
      title={external
        ? "Messages are sent by a collector running outside this application."
        : "Messages are collected through the Telegram account connected here."}
    >
      {external ? "External" : "Backend"}
    </span>
  );
}

function ConnectionSetup({
  apiId,
  setApiId,
  apiHash,
  setApiHash,
  phone,
  setPhone,
  challengeId,
  requiresPassword,
  code,
  setCode,
  password,
  setPassword,
  busy,
  onStart,
  onVerifyCode,
  onVerifyPassword,
  onCancel,
}) {
  return (
    <section className="surface telegram-card connection-setup-card">
      <div className="telegram-card-heading">
        <div className="telegram-heading-copy">
          <span className="telegram-section-icon"><TelegramIcon name="shield" /></span>
          <div>
            <span className="telegram-section-kicker">Backend Telegram account</span>
            <h2>{challengeId ? "Verify your account" : "Connect Telegram"}</h2>
            <p>{challengeId ? "Complete the secure sign-in step." : "Use your Telegram developer credentials."}</p>
          </div>
        </div>
        {!challengeId && (
          <button className="button button-ghost button-small" type="button" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
        )}
      </div>

      {!challengeId ? (
        <div className="connection-form">
          <label className="field">
            <FieldLabel help="The numeric app ID from your Telegram developer settings.">API ID</FieldLabel>
            <input value={apiId} inputMode="numeric" onChange={(event) => setApiId(event.target.value)} />
          </label>
          <label className="field">
            <FieldLabel help="The secret paired with your API ID. Keep it private.">API hash</FieldLabel>
            <input value={apiHash} type="password" autoComplete="off" onChange={(event) => setApiHash(event.target.value)} />
          </label>
          <label className="field">
            <span>Phone number</span>
            <input value={phone} placeholder="+49..." onChange={(event) => setPhone(event.target.value)} />
          </label>
          <button className="button button-primary" type="button" onClick={onStart} disabled={busy || !apiId || !apiHash || !phone}>
            Request verification code
          </button>
        </div>
      ) : !requiresPassword ? (
        <div className="verification-step">
          <div>
            <span className="step-label">Verification</span>
            <h3>Enter the code sent by Telegram</h3>
            <p>The code may appear in Telegram rather than as an SMS.</p>
          </div>
          <label className="field">
            <span>Verification code</span>
            <input value={code} autoComplete="one-time-code" autoFocus onChange={(event) => setCode(event.target.value)} />
          </label>
          <button className="button button-primary" type="button" onClick={onVerifyCode} disabled={busy || !code}>
            Verify code
          </button>
        </div>
      ) : (
        <div className="verification-step">
          <div>
            <span className="step-label">Two-step verification</span>
            <h3>Enter your Telegram password</h3>
            <p>This is required because two-step verification is enabled for the account.</p>
          </div>
          <label className="field">
            <span>Password</span>
            <input value={password} type="password" autoFocus onChange={(event) => setPassword(event.target.value)} />
          </label>
          <button className="button button-primary" type="button" onClick={onVerifyPassword} disabled={busy || !password}>
            Connect account
          </button>
        </div>
      )}
    </section>
  );
}

function ExternalCollectorState({ chats, connection, onShowBackendSetup }) {
  const hasChats = chats.length > 0;
  const online = Boolean(connection?.connected);
  const hasSource = hasChats || online;
  const stateLabel = online ? "Connected" : connection ? "Not reachable" : "Checking connection";
  const offlineDetail = connection?.last_seen_at
    ? ` Last contact: ${formatDate(connection.last_seen_at)}.`
    : " The collector has not contacted the application yet.";

  return (
    <section className={`surface telegram-card external-collector-card${hasSource ? " has-source" : ""}`}>
      <div className="telegram-heading-copy">
        <span className="telegram-section-icon telegram-section-icon-large"><TelegramIcon name="telegram" /></span>
        <div>
          <span className="telegram-section-kicker">
            Collection source
            {hasSource && (
              <InfoTooltip label="About external collectors">
                A separate collector sends messages here, so no Telegram account needs to be stored in this application.
              </InfoTooltip>
            )}
          </span>
          <h2>{hasSource ? `External collector ${online ? "connected" : "not reachable"}` : "No collection source yet"}</h2>
          <p>{hasSource ? `${chats.length} active collector chat${chats.length === 1 ? "" : "s"}. ${online ? "The collector is polling for work. No backend account required." : `New messages will not be collected until it reconnects.${offlineDetail}`}` : "Connect Telegram to add groups and channels here."}</p>
        </div>
      </div>
      <div className="external-collector-actions">
        {hasSource && (
          <span className={`connection-state connection-state-${online ? "ready" : "offline"}`}>
            <span className={`status-dot status-dot-${online ? "completed" : "error"}`} />
            {stateLabel}
          </span>
        )}
        <button className={`button ${hasSource ? "button-secondary" : "button-primary"}`} type="button" onClick={onShowBackendSetup}>
          {hasSource ? "Connect another account" : "Connect Telegram"}
        </button>
      </div>
    </section>
  );
}

function ConnectedAccount({ connection, externalChatCount, busy, onDisconnect }) {
  return (
    <section className="surface telegram-card account-card collection-source-card">
      <div className="telegram-heading-copy account-summary">
        <span className="telegram-section-icon telegram-section-icon-large"><TelegramIcon name="telegram" /></span>
        <div className="account-copy">
          <span className="telegram-section-kicker">Collection source</span>
          <h2>{connection.display_name || connection.phone || "Telegram account"}</h2>
          <p>
            {connection.phone || "Phone number unavailable"}
            {connection.last_verified_at ? ` · verified ${formatDate(connection.last_verified_at)}` : ""}
          </p>
          {externalChatCount > 0 && (
            <span className="connected-source-note">
              + External collector · {externalChatCount} chat{externalChatCount === 1 ? "" : "s"}
              <InfoTooltip label="About the additional external collector">
                These chats are supplied by a collector running outside this application.
              </InfoTooltip>
            </span>
          )}
        </div>
      </div>
      <div className="account-actions">
        <span className="connection-state connection-state-ready"><span className="status-dot status-dot-completed" /> Connected</span>
        <button className="button button-ghost button-small danger-text" type="button" onClick={onDisconnect} disabled={busy}>
          Disconnect
        </button>
      </div>
    </section>
  );
}

function AddChatSection({
  dialogs,
  selectedDialogId,
  setSelectedDialogId,
  initialSyncFrom,
  setInitialSyncFrom,
  interval,
  setInterval,
  busy,
  onLoadDialogs,
  onAddChat,
}) {
  return (
    <section className="surface telegram-card add-chat-card">
      <div className="telegram-card-heading">
        <div className="telegram-heading-copy">
          <span className="telegram-section-icon"><TelegramIcon name="chat" /></span>
          <div>
            <span className="telegram-section-kicker">Next step</span>
            <h2>Add a chat</h2>
            <p>Choose a group or channel to keep ready.</p>
          </div>
        </div>
        <button className="button button-secondary button-small" type="button" onClick={onLoadDialogs} disabled={busy}>
          {dialogs.length ? "Refresh list" : "Choose chats"}
        </button>
      </div>

      {dialogs.length ? (
        <div className="add-chat-form">
          <label className="field field-wide">
            <span>Group or channel</span>
            <select value={selectedDialogId} onChange={(event) => setSelectedDialogId(event.target.value)}>
              {dialogs.map((dialog) => (
                <option key={`${dialog.chat_type}-${dialog.telegram_chat_id}`} value={dialog.telegram_chat_id}>
                  {dialog.title} ({dialog.chat_type})
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <FieldLabel help="The first sync collects messages on or after this date.">Start history</FieldLabel>
            <input type="date" value={initialSyncFrom} onChange={(event) => setInitialSyncFrom(event.target.value)} />
          </label>
          <label className="field">
            <FieldLabel help="How often this chat checks for new messages.">Sync frequency</FieldLabel>
            <select value={interval} onChange={(event) => setInterval(Number(event.target.value))}>
              <option value={15}>Every 15 minutes</option>
              <option value={60}>Hourly</option>
              <option value={360}>Every 6 hours</option>
              <option value={1440}>Daily</option>
              <option value={0}>Automatic sync off</option>
            </select>
          </label>
          <button className="button button-primary" type="button" onClick={onAddChat} disabled={busy}>
            Add chat
          </button>
        </div>
      ) : (
        <div className="subtle-empty-state compact-empty-state">
          <span className="empty-state-icon"><TelegramIcon name="chat" /></span>
          <span>Choose from the groups and channels available to this account.</span>
        </div>
      )}
    </section>
  );
}

function CollectedChatsTable({ chats, busy, backendConnected, externalCollectorConnected, onSync, onUpdate }) {
  const [showArchived, setShowArchived] = useState(false);
  const [pendingIntervals, setPendingIntervals] = useState({});
  const archivedCount = chats.filter((chat) => chat.status === "archived").length;
  const activeCount = chats.length - archivedCount;
  const visibleChats = showArchived ? chats : chats.filter((chat) => chat.status !== "archived");

  const updateInterval = async (chatId, value) => {
    setPendingIntervals((current) => ({ ...current, [chatId]: value }));
    try {
      await onUpdate(chatId, { sync_interval_minutes: Number(value) });
    } finally {
      setPendingIntervals((current) => {
        const next = { ...current };
        delete next[chatId];
        return next;
      });
    }
  };

  return (
    <section className="surface telegram-card collected-chats-card">
      <div className="telegram-card-heading collected-chats-heading">
        <div className="telegram-heading-copy">
          <span className="telegram-section-icon"><TelegramIcon name="chat" /></span>
          <div>
            <h2>Collected chats</h2>
            <p>Messages from active chats are available for analysis.</p>
          </div>
        </div>
        <div className="section-heading-actions">
          <span className="table-count">{activeCount} active</span>
          {archivedCount > 0 && (
            <button className="button button-ghost button-small archive-toggle" type="button" onClick={() => setShowArchived((current) => !current)}>
              {showArchived ? "Hide archived" : `Show archived (${archivedCount})`}
            </button>
          )}
        </div>
      </div>

      {visibleChats.length ? (
        <div className="telegram-table-wrap">
          <table className="telegram-table">
            <thead>
              <tr>
                <th><span className="sr-only">Health</span>Chat</th>
                <th>Synchronization</th>
                <th>Frequency</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {visibleChats.map((chat) => {
                const needsBackendConnection = chat.ingest_mode !== "external_push" && !backendConnected;
                const needsExternalConnection = chat.ingest_mode === "external_push" && !externalCollectorConnected;
                const needsAttention = needsBackendConnection || needsExternalConnection || chat.status === "error" || Boolean(chat.last_error);
                const displayStatus = needsBackendConnection
                  ? "Reconnect needed"
                  : needsExternalConnection ? "External collector not reachable"
                  : needsAttention ? "Needs attention" : chatStatusText(chat.status);
                return (
                  <tr key={chat.id} className={chat.status === "archived" ? "is-archived" : ""}>
                    <td>
                      <div className="chat-identity">
                        <span className="chat-avatar chat-avatar-with-health">
                          <TelegramIcon name="chat" />
                          <span
                            className={`chat-health-dot status-dot status-dot-${needsAttention ? "error" : chat.status}`}
                            role="img"
                            aria-label={`Health: ${displayStatus}`}
                            title={displayStatus}
                          />
                        </span>
                        <div>
                          <strong title={chat.title}>{chat.title}</strong>
                          <span className="chat-meta">
                            <SourceBadge chat={chat} />
                            {chat.ingest_mode !== "external_push" && (chat.username ? `@${chat.username}` : chat.chat_type)}
                          </span>
                        </div>
                      </div>
                      {needsBackendConnection && <span className="table-error">Requires backend Telegram connection</span>}
                      {needsExternalConnection && <span className="table-error">External collector is not reachable</span>}
                      {chat.last_error && <span className="table-error">{chat.last_error}</span>}
                    </td>
                    <td>
                      <span className="sync-date"><strong>Last</strong>{formatDate(chat.last_sync_at)}</span>
                      <span className="sync-date sync-date-secondary">
                        <strong>Next</strong>
                        {nextSyncLabel(chat)}
                      </span>
                    </td>
                    <td>
                      <select
                        className="table-select"
                        value={pendingIntervals[chat.id] ?? syncIntervalValue(chat)}
                        onChange={(event) => updateInterval(chat.id, event.target.value)}
                        disabled={busy || chat.status === "archived"}
                        aria-label={`Sync interval for ${chat.title}`}
                      >
                        <option value={15}>Every 15 min</option>
                        <option value={60}>Hourly</option>
                        <option value={360}>Every 6 hours</option>
                        <option value={1440}>Daily</option>
                        <option value={0}>Automatic sync off</option>
                      </select>
                    </td>
                    <td>
                      <div className="table-actions">
                        <button
                          className="text-button row-action row-action-primary"
                          type="button"
                          onClick={() => onSync(chat)}
                          disabled={busy || chat.status === "archived" || needsBackendConnection}
                          title={needsBackendConnection
                            ? "Connect the backend Telegram account before syncing this chat."
                            : chat.ingest_mode === "external_push"
                              ? "Ask the external collector to fetch new messages."
                              : "Fetch new messages now."}
                        >
                          {chat.ingest_mode === "external_push" ? "Request sync" : "Sync now"}
                        </button>
                        <button
                          className="text-button row-action"
                          type="button"
                          onClick={() => onUpdate(chat.id, { archived: chat.status !== "archived" })}
                          disabled={busy}
                        >
                          {chat.status === "archived" ? "Reactivate" : "Archive"}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="subtle-empty-state compact-empty-state">
          <span className="empty-state-icon"><TelegramIcon name="chat" /></span>
          <span>{chats.length ? "No active chats. Show archived chats to restore one." : "No chats are being collected yet."}</span>
        </div>
      )}
    </section>
  );
}

function ScheduledReportsSection({
  chats,
  questionSets,
  schedules,
  backendConnected,
  busy,
  onSave,
  onDelete,
  onToggle,
  onOpenJob,
}) {
  const activeChats = chats.filter((chat) => (
    chat.status !== "archived" && (chat.ingest_mode === "external_push" || backendConnected)
  ));
  const [editingId, setEditingId] = useState(null);
  const [chatId, setChatId] = useState("");
  const [questionSetId, setQuestionSetId] = useState("");
  const [runTime, setRunTime] = useState("05:00");
  const [rollingWindowDays, setRollingWindowDays] = useState("1");
  const [enabled, setEnabled] = useState(true);
  const [allowPartialTelegramSync, setAllowPartialTelegramSync] = useState(false);
  const [formOpen, setFormOpen] = useState(false);

  useEffect(() => {
    if (!activeChats.some((chat) => chat.id === chatId)) {
      const nextChatId = activeChats[0]?.id || "";
      if (chatId !== nextChatId) setChatId(nextChatId);
    }
  }, [activeChats, chatId]);

  useEffect(() => {
    if (!questionSets.some((set) => set.id === questionSetId)) {
      const nextQuestionSetId = questionSets[0]?.id || "";
      if (questionSetId !== nextQuestionSetId) setQuestionSetId(nextQuestionSetId);
    }
  }, [questionSetId, questionSets]);

  const resetForm = () => {
    setEditingId(null);
    setChatId(activeChats[0]?.id || "");
    setQuestionSetId(questionSets[0]?.id || "");
    setRunTime("05:00");
    setRollingWindowDays("1");
    setEnabled(true);
    setAllowPartialTelegramSync(false);
    setFormOpen(false);
  };

  const openCreateForm = () => {
    setEditingId(null);
    setChatId(activeChats[0]?.id || "");
    setQuestionSetId(questionSets[0]?.id || "");
    setRunTime("05:00");
    setRollingWindowDays("1");
    setEnabled(true);
    setAllowPartialTelegramSync(false);
    setFormOpen(true);
  };

  const editSchedule = (schedule) => {
    setEditingId(schedule.id);
    setChatId(schedule.telegram_chat_id);
    setQuestionSetId(schedule.question_set_id);
    setRunTime(schedule.run_time_local);
    setRollingWindowDays(String(schedule.rolling_window_days));
    setEnabled(schedule.enabled);
    setAllowPartialTelegramSync(Boolean(schedule.allow_partial_telegram_sync));
    setFormOpen(true);
  };

  const save = async () => {
    const saved = await onSave(editingId, {
      telegram_chat_id: chatId,
      question_set_id: questionSetId,
      run_time_local: runTime,
      timezone: REPORT_TIMEZONE,
      rolling_window_days: Number(rollingWindowDays),
      enabled,
      allow_partial_telegram_sync: allowPartialTelegramSync,
    });
    if (saved) resetForm();
  };

  const chatTitle = (id) => chats.find((chat) => chat.id === id)?.title || "Telegram chat";
  const questionSetName = (id) => questionSets.find((set) => set.id === id)?.name || "Question set";
  const parsedWindowDays = Number(rollingWindowDays);
  const validWindowDays = Number.isSafeInteger(parsedWindowDays) && parsedWindowDays >= 1;
  const formReady = Boolean(
    activeChats.length && questionSets.length && chatId && questionSetId && runTime && validWindowDays
  );

  return (
    <section className="surface telegram-card scheduled-reports-card">
      <div className="telegram-card-heading">
        <div className="telegram-heading-copy">
          <span className="telegram-section-icon"><TelegramIcon name="calendar" /></span>
          <div>
            <span className="telegram-section-kicker">Optional</span>
            <h2>Scheduled reports</h2>
            <p>Run a saved question set automatically.</p>
          </div>
        </div>
        <div className="section-heading-actions">
          <span className="table-count">{schedules.filter((schedule) => schedule.enabled).length} enabled</span>
          {!formOpen && (
            <button className="button button-secondary button-small" type="button" onClick={openCreateForm} disabled={busy}>
              Add schedule
            </button>
          )}
        </div>
      </div>

      {(!activeChats.length || !questionSets.length) && (
        <div className="schedule-prerequisites" role="status">
          {!activeChats.length && <span><TelegramIcon name="chat" /> Add an active chat first.</span>}
          {!questionSets.length && <span><TelegramIcon name="pulse" /> Create a question set in New Analysis first.</span>}
        </div>
      )}

      {formOpen && (
        <div className="schedule-editor">
          <div className="schedule-editor-heading">
            <div>
              <span className="telegram-section-kicker">{editingId ? "Editing" : "New automation"}</span>
              <h3>{editingId ? "Edit schedule" : "Add a scheduled report"}</h3>
            </div>
            <button className="button button-ghost button-small" type="button" onClick={resetForm} disabled={busy}>
              Cancel
            </button>
          </div>

          <div className="schedule-form">
            <fieldset className="schedule-form-group schedule-form-report">
              <legend>Report</legend>
              <label className="field">
                <span>Group or channel</span>
                <select value={chatId} onChange={(event) => setChatId(event.target.value)} disabled={!activeChats.length}>
                  {activeChats.length ? (
                    activeChats.map((chat) => (
                      <option key={chat.id} value={chat.id}>
                        {chat.title} ({chatSourceLabel(chat)})
                      </option>
                    ))
                  ) : (
                    <option value="">No active chats</option>
                  )}
                </select>
              </label>
              <label className="field">
                <FieldLabel help="The saved questions each report will answer.">Question set</FieldLabel>
                <select value={questionSetId} onChange={(event) => setQuestionSetId(event.target.value)} disabled={!questionSets.length}>
                  {questionSets.length ? (
                    questionSets.map((set) => <option key={set.id} value={set.id}>{set.name}</option>)
                  ) : (
                    <option value="">No question sets</option>
                  )}
                </select>
              </label>
            </fieldset>

            <fieldset className="schedule-form-group schedule-form-timing">
              <legend>Timing</legend>
              <label className="field">
                <span>Run time</span>
                <input type="time" value={runTime} onChange={(event) => setRunTime(event.target.value)} />
              </label>
              <label className="field schedule-window-field">
                <FieldLabel help="This controls both how often the report runs and how far back it looks.">Frequency and range</FieldLabel>
                <div className="schedule-days-input">
                  <input
                    type="number"
                    min="1"
                    step="1"
                    value={rollingWindowDays}
                    onChange={(event) => setRollingWindowDays(event.target.value)}
                    aria-describedby="schedule-days-hint"
                  />
                  <span>days</span>
                </div>
                <small id="schedule-days-hint">
                  Runs every {validWindowDays ? rollingWindowLabel(parsedWindowDays) : "chosen number of days"} and includes the same period.
                </small>
              </label>
            </fieldset>

            <fieldset className="schedule-form-group schedule-form-options">
              <legend>Options</legend>
              <label className="option-row schedule-enabled">
                <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
                <span>Enabled</span>
              </label>
              <label className="option-row schedule-enabled">
                <input
                  type="checkbox"
                  checked={allowPartialTelegramSync}
                  onChange={(event) => setAllowPartialTelegramSync(event.target.checked)}
                />
                <span className="field-label-copy">
                  Allow partial report
                  <InfoTooltip label="About partial reports">
                    Run with stored messages instead of waiting for collection to catch up. Recent messages may be missing.
                  </InfoTooltip>
                </span>
              </label>
            </fieldset>
          </div>

          <div className="schedule-form-actions">
            <button className="button button-primary" type="button" onClick={save} disabled={busy || !formReady}>
              {editingId ? "Update schedule" : "Add schedule"}
            </button>
          </div>
        </div>
      )}

      {schedules.length ? (
        <div className="telegram-table-wrap schedule-table-wrap">
          <table className="telegram-table schedule-table">
            <thead>
              <tr>
                <th>Report</th>
                <th>Timing</th>
                <th>Window</th>
                <th>Next run</th>
                <th>Status</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {schedules.map((schedule) => (
                <tr key={schedule.id} className={!schedule.enabled ? "is-archived" : ""}>
                  <td>
                    <strong>{chatTitle(schedule.telegram_chat_id)}</strong>
                    <span>{questionSetName(schedule.question_set_id)}</span>
                    {schedule.last_error && <span className="table-error">{schedule.last_error}</span>}
                  </td>
                  <td>{schedule.run_time_local}</td>
                  <td>
                    <span>{scheduleIntervalLabel(schedule.rolling_window_days)}</span>
                    {schedule.allow_partial_telegram_sync && (
                      <span className="partial-badge" title="May run before the latest Telegram synchronization finishes.">Partial allowed</span>
                    )}
                  </td>
                  <td>
                    <span>{schedule.enabled ? formatDate(schedule.next_run_at) : "-"}</span>
                    <span className="table-secondary-line">Last · {formatDate(schedule.last_run_at)}</span>
                  </td>
                  <td>
                    <span className={`table-status table-status-${schedule.last_error ? "error" : schedule.enabled ? "active" : "archived"}`}>
                      <span className={`status-dot status-dot-${schedule.last_error ? "error" : schedule.enabled ? "active" : "archived"}`} />
                      {schedule.last_error ? "Needs attention" : schedule.enabled ? "Enabled" : "Paused"}
                    </span>
                  </td>
                  <td>
                    <div className="table-actions schedule-actions">
                      <button className="text-button row-action row-action-primary" type="button" onClick={() => editSchedule(schedule)} disabled={busy}>
                        Edit
                      </button>
                      <button className="text-button row-action" type="button" onClick={() => onToggle(schedule)} disabled={busy}>
                        {schedule.enabled ? "Pause" : "Enable"}
                      </button>
                      {schedule.last_job_id && (
                        <button
                          className="text-button row-action"
                          type="button"
                          onClick={() => onOpenJob(schedule.last_job_id)}
                        >
                          Last job
                        </button>
                      )}
                      <button className="text-button row-action danger-text" type="button" onClick={() => onDelete(schedule)} disabled={busy}>
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        !formOpen && (
          <div className="subtle-empty-state compact-empty-state">
            <span className="empty-state-icon"><TelegramIcon name="calendar" /></span>
            <span>No automatic reports yet.</span>
            <button className="button button-secondary button-small" type="button" onClick={openCreateForm} disabled={busy}>
              Add schedule
            </button>
          </div>
        )
      )}
    </section>
  );
}

export function TelegramSourcesPanel({
  connection,
  collectorConnection,
  chats,
  schedules,
  questionSets,
  request,
  onRefresh,
  onSelectJob,
  showToast,
}) {
  const [apiId, setApiId] = useState("");
  const [apiHash, setApiHash] = useState("");
  const [phone, setPhone] = useState("");
  const [challengeId, setChallengeId] = useState(null);
  const [requiresPassword, setRequiresPassword] = useState(false);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [dialogs, setDialogs] = useState([]);
  const [selectedDialogId, setSelectedDialogId] = useState("");
  const [initialSyncFrom, setInitialSyncFrom] = useState(() => {
    const date = new Date();
    date.setDate(date.getDate() - 30);
    return date.toISOString().slice(0, 10);
  });
  const [interval, setInterval] = useState(60);
  const [busy, setBusy] = useState(false);
  const [showBackendSetup, setShowBackendSetup] = useState(false);
  const activeExternalChats = chats.filter((chat) => (
    chat.ingest_mode === "external_push" && chat.status !== "archived"
  ));
  const isBackendConnected = Boolean(connection?.connected);
  const isExternalCollectorConnected = Boolean(collectorConnection?.connected);
  const activeChats = chats.filter((chat) => chat.status !== "archived");
  const usableActiveChats = activeChats.filter((chat) => (
    chat.ingest_mode === "external_push" ? isExternalCollectorConnected : isBackendConnected
  ));
  const unavailableBackendChats = activeChats.filter((chat) => (
    chat.ingest_mode !== "external_push" && !isBackendConnected
  ));
  const chatsWithIssues = activeChats.filter((chat) => chat.status === "error" || chat.last_error);
  const hasCollectionIssues = unavailableBackendChats.length > 0
    || (activeExternalChats.length > 0 && !isExternalCollectorConnected)
    || chatsWithIssues.length > 0;
  const hasExternalSource = isExternalCollectorConnected || activeExternalChats.length > 0;
  const sourceLabel = isBackendConnected && hasExternalSource
    ? "Backend + external"
    : isBackendConnected ? "Backend account" : hasExternalSource ? "External collector" : "Not connected";
  const enabledScheduleCount = schedules.filter((schedule) => schedule.enabled).length;
  const sourceHasIssues = unavailableBackendChats.length > 0
    || (activeExternalChats.length > 0 && !isExternalCollectorConnected);
  const chatsHaveIssues = chatsWithIssues.length > 0
    || (activeChats.length > 0 && usableActiveChats.length === 0);
  const readinessState = hasCollectionIssues
    ? "attention"
    : usableActiveChats.length > 0 ? "ready" : "setup";
  const readinessBadge = readinessState === "ready"
    ? "Ready"
    : readinessState === "attention" ? "Attention needed" : "Setup needed";
  const workspaceSubtitle = readinessState === "attention"
    ? `${sourceLabel} · collection needs attention`
    : readinessState === "ready"
      ? `${sourceLabel} · ${usableActiveChats.length} usable chat${usableActiveChats.length === 1 ? "" : "s"}`
      : sourceLabel === "Not connected" ? "Connect a collection source to begin" : "Add a chat to begin collecting";
  const shouldShowBackendSetup = showBackendSetup || Boolean(challengeId);

  const run = async (action) => {
    setBusy(true);
    try {
      await action();
      return true;
    } catch (error) {
      showToast(error.message, "error");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const startLogin = () => run(async () => {
    const result = await request("/telegram/connection/start", {
      method: "POST",
      body: { api_id: Number(apiId), api_hash: apiHash, phone },
    });
    setChallengeId(result.challenge_id);
    setRequiresPassword(false);
    showToast("Telegram verification code requested");
  });

  const verifyCode = () => run(async () => {
    const result = await request("/telegram/connection/code", {
      method: "POST",
      body: { challenge_id: challengeId, code },
    });
    if (result.requires_password) {
      setRequiresPassword(true);
      showToast("Two-step verification password required");
      return;
    }
    setChallengeId(null);
    setCode("");
    setShowBackendSetup(false);
    await onRefresh();
    showToast("Telegram account connected");
  });

  const verifyPassword = () => run(async () => {
    await request("/telegram/connection/password", {
      method: "POST",
      body: { challenge_id: challengeId, password },
    });
    setChallengeId(null);
    setRequiresPassword(false);
    setPassword("");
    setShowBackendSetup(false);
    await onRefresh();
    showToast("Telegram account connected");
  });

  const loadDialogs = () => run(async () => {
    const result = await request("/telegram/dialogs");
    setDialogs(result);
    if (result.length) setSelectedDialogId(String(result[0].telegram_chat_id));
  });

  const addChat = () => run(async () => {
    const dialog = dialogs.find((item) => String(item.telegram_chat_id) === selectedDialogId);
    if (!dialog) throw new Error("Select a group or channel");
    await request("/telegram/chats", {
      method: "POST",
      body: {
        ...dialog,
        initial_sync_from: new Date(`${initialSyncFrom}T00:00:00`).toISOString(),
        sync_interval_minutes: Number(interval),
      },
    });
    await onRefresh();
    showToast("Chat added to collection");
  });

  const disconnect = () => run(async () => {
    if (!window.confirm("Disconnect Telegram and revoke the stored session?")) return;
    await request("/telegram/connection", { method: "DELETE" });
    setDialogs([]);
    setShowBackendSetup(false);
    await onRefresh();
    showToast("Telegram account disconnected");
  });

  const syncChat = (chat) => run(async () => {
    await request(`/telegram/chats/${chat.id}/sync`, { method: "POST" });
    await onRefresh();
    showToast(chat.ingest_mode === "external_push" ? "Collector synchronization requested" : "Synchronization scheduled");
  });

  const updateChat = (chatId, body) => run(async () => {
    await request(`/telegram/chats/${chatId}`, { method: "PATCH", body });
    await onRefresh();
    showToast(body.archived ? "Chat archived" : "Chat settings updated");
  });

  const saveSchedule = (scheduleId, body) => run(async () => {
    await request(scheduleId ? `/telegram/report-schedules/${scheduleId}` : "/telegram/report-schedules", {
      method: scheduleId ? "PATCH" : "POST",
      body,
    });
    await onRefresh();
    showToast(scheduleId ? "Report schedule updated" : "Report schedule added");
  });

  const deleteSchedule = (schedule) => run(async () => {
    if (!window.confirm(`Delete the scheduled report for ${schedule.run_time_local}?`)) return;
    await request(`/telegram/report-schedules/${schedule.id}`, { method: "DELETE" });
    await onRefresh();
    showToast("Report schedule deleted");
  });

  const toggleSchedule = (schedule) => run(async () => {
    await request(`/telegram/report-schedules/${schedule.id}`, {
      method: "PATCH",
      body: { enabled: !schedule.enabled },
    });
    await onRefresh();
    showToast(schedule.enabled ? "Report schedule paused" : "Report schedule enabled");
  });

  const refreshCollector = () => run(async () => {
    await onRefresh();
    showToast("Telegram status refreshed");
  });

  return (
    <section className={`page workspace-page workspace-page-${readinessState} telegram-page telegram-page-${readinessState}`}>
      <WorkspaceTopbar
        tone={readinessState}
        badge={readinessBadge}
        title="Telegram Collector"
        subtitle={workspaceSubtitle}
        actions={(
          <button className="button button-secondary" type="button" onClick={refreshCollector} disabled={busy}>
            Manual refresh
          </button>
        )}
      />

      <WorkspaceRail
        className="telegram-readiness-rail"
        ariaLabel="Telegram collector readiness"
        items={[
          {
            key: "source",
            label: "Source",
            status: sourceHasIssues ? "failed" : sourceLabel !== "Not connected" ? "completed" : "active",
            detail: sourceHasIssues ? "Connection unavailable" : sourceLabel,
            progress: sourceHasIssues ? 20 : sourceLabel !== "Not connected" ? 100 : 0,
          },
          {
            key: "chats",
            label: "Chats",
            status: chatsHaveIssues ? "failed" : usableActiveChats.length > 0 ? "completed" : "active",
            detail: chatsHaveIssues
              ? "Collection issue"
              : usableActiveChats.length > 0
                ? `${usableActiveChats.length} usable`
                : "Add at least one chat",
            progress: chatsHaveIssues ? 20 : usableActiveChats.length > 0 ? 100 : 0,
          },
          {
            key: "automation",
            label: "Automation",
            status: enabledScheduleCount > 0 ? "completed" : "optional",
            detail: enabledScheduleCount > 0
              ? `${enabledScheduleCount} enabled`
              : "Optional",
            progress: enabledScheduleCount > 0 ? 100 : 0,
          },
        ]}
      />

      <div className="telegram-primary-workspace">
        {isBackendConnected ? (
          <div className="telegram-setup-grid">
            <ConnectedAccount
              connection={connection}
              externalChatCount={activeExternalChats.length}
              busy={busy}
              onDisconnect={disconnect}
            />
            <AddChatSection
              dialogs={dialogs}
              selectedDialogId={selectedDialogId}
              setSelectedDialogId={setSelectedDialogId}
              initialSyncFrom={initialSyncFrom}
              setInitialSyncFrom={setInitialSyncFrom}
              interval={interval}
              setInterval={setInterval}
              busy={busy}
              onLoadDialogs={loadDialogs}
              onAddChat={addChat}
            />
          </div>
        ) : shouldShowBackendSetup ? (
          <div className="telegram-source-stack">
            <ConnectionSetup
              apiId={apiId}
              setApiId={setApiId}
              apiHash={apiHash}
              setApiHash={setApiHash}
              phone={phone}
              setPhone={setPhone}
              challengeId={challengeId}
              requiresPassword={requiresPassword}
              code={code}
              setCode={setCode}
              password={password}
              setPassword={setPassword}
              busy={busy}
              onStart={startLogin}
              onVerifyCode={verifyCode}
              onVerifyPassword={verifyPassword}
              onCancel={() => setShowBackendSetup(false)}
            />
          </div>
        ) : (
          <div className="telegram-source-stack">
            <ExternalCollectorState
              chats={activeExternalChats}
              connection={collectorConnection}
              onShowBackendSetup={() => setShowBackendSetup(true)}
            />
          </div>
        )}
      </div>
      <div className="telegram-data-grid">
        <CollectedChatsTable
          chats={chats}
          busy={busy}
          backendConnected={isBackendConnected}
          externalCollectorConnected={isExternalCollectorConnected}
          onSync={syncChat}
          onUpdate={updateChat}
        />
        <ScheduledReportsSection
          chats={chats}
          questionSets={questionSets}
          schedules={schedules}
          backendConnected={isBackendConnected}
          busy={busy}
          onSave={saveSchedule}
          onDelete={deleteSchedule}
          onToggle={toggleSchedule}
          onOpenJob={onSelectJob}
        />
      </div>
    </section>
  );
}
