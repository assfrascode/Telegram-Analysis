import { useEffect, useId, useState } from "react";
import { formatDate } from "../lib/format";

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

function browserTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function rollingWindowLabel(days) {
  return Number(days) === 1 ? "1 day" : `${days} days`;
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

function CollectorOverview({ state, sourceLabel, activeChatCount, enabledScheduleCount }) {
  const copy = {
    ready: {
      badge: "Ready",
      title: "Chats are ready for analysis",
      description: "Collection is active and reports can use the stored messages.",
    },
    attention: {
      badge: "Attention needed",
      title: "Collection needs attention",
      description: "Check the highlighted chat issue before relying on new messages.",
    },
    setup: {
      badge: "Setup needed",
      title: sourceLabel === "Not connected" ? "Connect a Telegram source" : "Add a chat to begin",
      description: sourceLabel === "Not connected"
        ? "Connect an account, or start an external collector."
        : "Your source is connected and ready for a group or channel.",
    },
  }[state];

  return (
    <section className={`collector-overview collector-overview-${state}`}>
      <div className="collector-readiness">
        <span className="collector-hero-icon"><TelegramIcon name={state === "attention" ? "pulse" : "telegram"} /></span>
        <div>
          <span className={`readiness-badge readiness-badge-${state}`}>
            <span className="status-dot" />
            {copy.badge}
          </span>
          <h2>{copy.title}</h2>
          <p>{copy.description}</p>
        </div>
      </div>
      <div className="collector-metrics" aria-label="Telegram collector summary">
        <div>
          <span>Collection source</span>
          <strong>{sourceLabel}</strong>
        </div>
        <div>
          <span>Active chats</span>
          <strong>{activeChatCount}</strong>
        </div>
        <div>
          <span>Auto reports</span>
          <strong>{enabledScheduleCount}</strong>
        </div>
      </div>
    </section>
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

function ExternalCollectorState({ chats, onShowBackendSetup, showBackendSetup }) {
  const hasChats = chats.length > 0;

  return (
    <section className={`surface telegram-card external-collector-card${hasChats ? " has-source" : ""}`}>
      <div className="telegram-heading-copy">
        <span className="telegram-section-icon telegram-section-icon-large"><TelegramIcon name="telegram" /></span>
        <div>
          <span className="telegram-section-kicker">
            Collection source
            {hasChats && (
              <InfoTooltip label="About external collectors">
                A separate collector sends messages here, so no Telegram account needs to be stored in this application.
              </InfoTooltip>
            )}
          </span>
          <h2>{hasChats ? "External collector connected" : "No collection source yet"}</h2>
          <p>{hasChats ? `${chats.length} active collector chat${chats.length === 1 ? "" : "s"}. No backend account required.` : "Connect Telegram to add groups and channels here."}</p>
        </div>
      </div>
      <div className="external-collector-actions">
        {!showBackendSetup && (
          <button className={`button ${hasChats ? "button-secondary" : "button-primary"}`} type="button" onClick={onShowBackendSetup}>
            {hasChats ? "Connect another account" : "Connect Telegram"}
          </button>
        )}
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

function CollectedChatsTable({ chats, busy, backendConnected, onSync, onUpdate }) {
  const [showArchived, setShowArchived] = useState(false);
  const archivedCount = chats.filter((chat) => chat.status === "archived").length;
  const activeCount = chats.length - archivedCount;
  const visibleChats = showArchived ? chats : chats.filter((chat) => chat.status !== "archived");

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
                <th>Chat</th>
                <th>Health</th>
                <th>Synchronization</th>
                <th>Frequency</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {visibleChats.map((chat) => {
                const needsBackendConnection = chat.ingest_mode !== "external_push" && !backendConnected;
                const needsAttention = needsBackendConnection || chat.status === "error" || Boolean(chat.last_error);
                const displayStatus = needsBackendConnection
                  ? "Reconnect needed"
                  : needsAttention ? "Needs attention" : chatStatusText(chat.status);
                return (
                  <tr key={chat.id} className={chat.status === "archived" ? "is-archived" : ""}>
                    <td>
                      <div className="chat-identity">
                        <span className="chat-avatar"><TelegramIcon name="chat" /></span>
                        <div>
                          <strong title={chat.title}>{chat.title}</strong>
                          <span className="chat-meta">
                            <SourceBadge chat={chat} />
                            {chat.ingest_mode !== "external_push" && (chat.username ? `@${chat.username}` : chat.chat_type)}
                          </span>
                        </div>
                      </div>
                      {needsBackendConnection && <span className="table-error">Requires backend Telegram connection</span>}
                      {chat.last_error && <span className="table-error">{chat.last_error}</span>}
                    </td>
                    <td>
                      <span className={`table-status table-status-${needsAttention ? "error" : chat.status}`}>
                        <span className={`status-dot status-dot-${needsAttention ? "error" : chat.status}`} />
                        {displayStatus}
                      </span>
                    </td>
                    <td>
                      <span className="sync-date"><strong>Last</strong>{formatDate(chat.last_sync_at)}</span>
                      <span className="sync-date sync-date-secondary">
                        <strong>Next</strong>
                        {chat.status === "syncing" || chat.status === "archived" ? "-" : formatDate(chat.next_sync_at)}
                      </span>
                    </td>
                    <td>
                      <select
                        className="table-select"
                        value={chat.sync_interval_minutes}
                        onChange={(event) => onUpdate(chat.id, { sync_interval_minutes: Number(event.target.value) })}
                        disabled={busy || chat.status === "archived"}
                        aria-label={`Sync interval for ${chat.title}`}
                      >
                        <option value={15}>Every 15 min</option>
                        <option value={60}>Hourly</option>
                        <option value={360}>Every 6 hours</option>
                        <option value={1440}>Daily</option>
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
  const [timezone, setTimezone] = useState(browserTimezone);
  const [rollingWindowDays, setRollingWindowDays] = useState(1);
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
    setTimezone(browserTimezone());
    setRollingWindowDays(1);
    setEnabled(true);
    setAllowPartialTelegramSync(false);
    setFormOpen(false);
  };

  const openCreateForm = () => {
    setEditingId(null);
    setChatId(activeChats[0]?.id || "");
    setQuestionSetId(questionSets[0]?.id || "");
    setRunTime("05:00");
    setTimezone(browserTimezone());
    setRollingWindowDays(1);
    setEnabled(true);
    setAllowPartialTelegramSync(false);
    setFormOpen(true);
  };

  const editSchedule = (schedule) => {
    setEditingId(schedule.id);
    setChatId(schedule.telegram_chat_id);
    setQuestionSetId(schedule.question_set_id);
    setRunTime(schedule.run_time_local);
    setTimezone(schedule.timezone);
    setRollingWindowDays(schedule.rolling_window_days);
    setEnabled(schedule.enabled);
    setAllowPartialTelegramSync(Boolean(schedule.allow_partial_telegram_sync));
    setFormOpen(true);
  };

  const save = async () => {
    const saved = await onSave(editingId, {
      telegram_chat_id: chatId,
      question_set_id: questionSetId,
      run_time_local: runTime,
      timezone,
      rolling_window_days: Number(rollingWindowDays),
      enabled,
      allow_partial_telegram_sync: allowPartialTelegramSync,
    });
    if (saved) resetForm();
  };

  const chatTitle = (id) => chats.find((chat) => chat.id === id)?.title || "Telegram chat";
  const questionSetName = (id) => questionSets.find((set) => set.id === id)?.name || "Question set";
  const formReady = Boolean(activeChats.length && questionSets.length && chatId && questionSetId && runTime && timezone);

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
              <label className="field">
                <FieldLabel help="The report runs at this local time in the selected timezone.">Timezone</FieldLabel>
                <input value={timezone} onChange={(event) => setTimezone(event.target.value)} />
              </label>
              <label className="field schedule-window-field">
                <FieldLabel help="This controls both how often the report runs and how far back it looks.">Frequency and range</FieldLabel>
                <select value={rollingWindowDays} onChange={(event) => setRollingWindowDays(Number(event.target.value))}>
                  <option value={1}>Daily · previous day</option>
                  <option value={7}>Every 7 days · previous 7 days</option>
                  <option value={14}>Every 14 days · previous 14 days</option>
                  <option value={30}>Every 30 days · previous 30 days</option>
                </select>
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
                  <td>{schedule.run_time_local} <span className="muted-inline">{schedule.timezone}</span></td>
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
  const activeChats = chats.filter((chat) => chat.status !== "archived");
  const usableActiveChats = activeChats.filter((chat) => (
    chat.ingest_mode === "external_push" || isBackendConnected
  ));
  const unavailableBackendChats = activeChats.filter((chat) => (
    chat.ingest_mode !== "external_push" && !isBackendConnected
  ));
  const chatsWithIssues = activeChats.filter((chat) => chat.status === "error" || chat.last_error);
  const hasCollectionIssues = unavailableBackendChats.length > 0 || chatsWithIssues.length > 0;
  const hasExternalSource = activeExternalChats.length > 0;
  const sourceLabel = isBackendConnected && hasExternalSource
    ? "Backend + external"
    : isBackendConnected ? "Backend account" : hasExternalSource ? "External collector" : "Not connected";
  const readinessState = hasCollectionIssues
    ? "attention"
    : usableActiveChats.length > 0 ? "ready" : "setup";
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

  return (
    <section className="page telegram-page">
      <header className="page-header">
        <div>
          <span className="page-kicker">Telegram setup</span>
          <h1>Telegram collector</h1>
          <p>Keep groups and channels ready for analysis.</p>
        </div>
      </header>

      <CollectorOverview
        state={readinessState}
        sourceLabel={sourceLabel}
        activeChatCount={usableActiveChats.length}
        enabledScheduleCount={schedules.filter((schedule) => schedule.enabled).length}
      />

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
      ) : (
        <div className="telegram-source-stack">
          <ExternalCollectorState
            chats={activeExternalChats}
            onShowBackendSetup={() => setShowBackendSetup(true)}
            showBackendSetup={shouldShowBackendSetup}
          />
          {shouldShowBackendSetup && (
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
          )}
        </div>
      )}
      <CollectedChatsTable
        chats={chats}
        busy={busy}
        backendConnected={isBackendConnected}
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
    </section>
  );
}
