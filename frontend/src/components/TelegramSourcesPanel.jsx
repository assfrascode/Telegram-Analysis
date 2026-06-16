import { useEffect, useState } from "react";
import { formatDate } from "../lib/format";

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
}) {
  return (
    <section className="surface telegram-card connection-setup-card">
      <div className="section-heading">
        <div>
          <span className="section-index">01</span>
          <div>
            <h2>Connect Telegram</h2>
            <p>Use the API credentials for your application from my.telegram.org.</p>
          </div>
        </div>
      </div>

      {!challengeId ? (
        <div className="connection-form">
          <label className="field">
            <span>API ID</span>
            <input value={apiId} inputMode="numeric" onChange={(event) => setApiId(event.target.value)} />
          </label>
          <label className="field">
            <span>API hash</span>
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

function ConnectedAccount({ connection, busy, onDisconnect }) {
  return (
    <section className="surface telegram-card account-card">
      <div className="account-summary">
        <span className="account-avatar">T</span>
        <div>
          <span className="page-kicker">Connected account</span>
          <h2>{connection.display_name || connection.phone || "Telegram account"}</h2>
          <p>
            {connection.phone || "Phone number unavailable"}
            {connection.last_verified_at ? ` - verified ${formatDate(connection.last_verified_at)}` : ""}
          </p>
        </div>
      </div>
      <div className="account-actions">
        <span className="connection-state"><span className="status-dot status-dot-completed" /> Connected</span>
        <button className="button button-danger button-small" type="button" onClick={onDisconnect} disabled={busy}>
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
    <section className="surface telegram-card">
      <div className="section-heading telegram-section-heading">
        <div>
          <span className="section-index">02</span>
          <div>
            <h2>Add a group or channel</h2>
            <p>Choose which Telegram conversations should be collected for future analyses.</p>
          </div>
        </div>
        <button className="button button-secondary button-small" type="button" onClick={onLoadDialogs} disabled={busy}>
          {dialogs.length ? "Reload available chats" : "Load available chats"}
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
            <span>Collect messages from</span>
            <input type="date" value={initialSyncFrom} onChange={(event) => setInitialSyncFrom(event.target.value)} />
          </label>
          <label className="field">
            <span>Sync interval</span>
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
        <div className="subtle-empty-state">Load your available Telegram groups and channels to add one here.</div>
      )}
    </section>
  );
}

function CollectedChatsTable({ chats, busy, onSync, onUpdate }) {
  return (
    <section className="surface telegram-card collected-chats-card">
      <div className="section-heading">
        <div>
          <span className="section-index">03</span>
          <div>
            <h2>Collected chats</h2>
            <p>Manage synchronization and availability for report creation.</p>
          </div>
        </div>
        <span className="table-count">{chats.length} total</span>
      </div>

      {chats.length ? (
        <div className="telegram-table-wrap">
          <table className="telegram-table">
            <thead>
              <tr>
                <th>Chat</th>
                <th>Status</th>
                <th>Last sync</th>
                <th>Next sync</th>
                <th>Interval</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {chats.map((chat) => (
                <tr key={chat.id} className={chat.status === "archived" ? "is-archived" : ""}>
                  <td>
                    <strong>{chat.title}</strong>
                    <span>{chat.username ? `@${chat.username}` : chat.chat_type}</span>
                    {chat.last_error && <span className="table-error">{chat.last_error}</span>}
                  </td>
                  <td>
                    <span className={`table-status table-status-${chat.status}`}>
                      <span className={`status-dot status-dot-${chat.status}`} />
                      {chatStatusText(chat.status)}
                    </span>
                  </td>
                  <td>{formatDate(chat.last_sync_at)}</td>
                  <td>{chat.status === "syncing" || chat.status === "archived" ? "-" : formatDate(chat.next_sync_at)}</td>
                  <td>
                    <select
                      className="table-select"
                      value={chat.sync_interval_minutes}
                      onChange={(event) => onUpdate(chat.id, { sync_interval_minutes: Number(event.target.value) })}
                      disabled={busy || chat.status === "archived"}
                      aria-label={`Sync interval for ${chat.title}`}
                    >
                      <option value={15}>15 min</option>
                      <option value={60}>Hourly</option>
                      <option value={360}>6 hours</option>
                      <option value={1440}>Daily</option>
                    </select>
                  </td>
                  <td>
                    <div className="table-actions">
                      <button
                        className="text-button"
                        type="button"
                        onClick={() => onSync(chat.id)}
                        disabled={busy || chat.status === "archived"}
                      >
                        Sync now
                      </button>
                      <button
                        className="text-button"
                        type="button"
                        onClick={() => onUpdate(chat.id, { archived: chat.status !== "archived" })}
                        disabled={busy}
                      >
                        {chat.status === "archived" ? "Reactivate" : "Archive"}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="subtle-empty-state">No chats are being collected yet.</div>
      )}
    </section>
  );
}

function ScheduledReportsSection({
  chats,
  questionSets,
  schedules,
  busy,
  onSave,
  onDelete,
  onToggle,
  onOpenJob,
}) {
  const activeChats = chats.filter((chat) => chat.status !== "archived");
  const [editingId, setEditingId] = useState(null);
  const [chatId, setChatId] = useState("");
  const [questionSetId, setQuestionSetId] = useState("");
  const [runTime, setRunTime] = useState("05:00");
  const [timezone, setTimezone] = useState(browserTimezone);
  const [rollingWindowDays, setRollingWindowDays] = useState(1);
  const [enabled, setEnabled] = useState(true);

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
  };

  const editSchedule = (schedule) => {
    setEditingId(schedule.id);
    setChatId(schedule.telegram_chat_id);
    setQuestionSetId(schedule.question_set_id);
    setRunTime(schedule.run_time_local);
    setTimezone(schedule.timezone);
    setRollingWindowDays(schedule.rolling_window_days);
    setEnabled(schedule.enabled);
  };

  const save = async () => {
    const saved = await onSave(editingId, {
      telegram_chat_id: chatId,
      question_set_id: questionSetId,
      run_time_local: runTime,
      timezone,
      rolling_window_days: Number(rollingWindowDays),
      enabled,
    });
    if (saved) resetForm();
  };

  const chatTitle = (id) => chats.find((chat) => chat.id === id)?.title || "Telegram chat";
  const questionSetName = (id) => questionSets.find((set) => set.id === id)?.name || "Question set";
  const formReady = Boolean(activeChats.length && questionSets.length && chatId && questionSetId && runTime && timezone);

  return (
    <section className="surface telegram-card scheduled-reports-card">
      <div className="section-heading telegram-section-heading">
        <div>
          <span className="section-index">04</span>
          <div>
            <h2>Scheduled reports</h2>
            <p>Generate recurring Telegram reports from collected chats and saved question sets.</p>
          </div>
        </div>
      </div>

      <div className="schedule-form">
        <label className="field field-wide">
          <span>Group or channel</span>
          <select value={chatId} onChange={(event) => setChatId(event.target.value)} disabled={!activeChats.length}>
            {activeChats.length ? (
              activeChats.map((chat) => <option key={chat.id} value={chat.id}>{chat.title}</option>)
            ) : (
              <option value="">No active chats</option>
            )}
          </select>
        </label>
        <label className="field field-wide">
          <span>Question set</span>
          <select value={questionSetId} onChange={(event) => setQuestionSetId(event.target.value)} disabled={!questionSets.length}>
            {questionSets.length ? (
              questionSets.map((set) => <option key={set.id} value={set.id}>{set.name}</option>)
            ) : (
              <option value="">No question sets</option>
            )}
          </select>
        </label>
        <label className="field">
          <span>Run time</span>
          <input type="time" value={runTime} onChange={(event) => setRunTime(event.target.value)} />
        </label>
        <label className="field">
          <span>Timezone</span>
          <input value={timezone} onChange={(event) => setTimezone(event.target.value)} />
        </label>
        <label className="field">
          <span>Report window</span>
          <select value={rollingWindowDays} onChange={(event) => setRollingWindowDays(Number(event.target.value))}>
            <option value={1}>Last 1 day</option>
            <option value={7}>Last 7 days</option>
            <option value={14}>Last 14 days</option>
            <option value={30}>Last 30 days</option>
          </select>
        </label>
        <label className="option-row schedule-enabled">
          <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
          <span>Enabled</span>
        </label>
        <div className="schedule-form-actions">
          {editingId && (
            <button className="button button-ghost button-small" type="button" onClick={resetForm} disabled={busy}>
              Cancel edit
            </button>
          )}
          <button className="button button-primary button-small" type="button" onClick={save} disabled={busy || !formReady}>
            {editingId ? "Update schedule" : "Add schedule"}
          </button>
        </div>
      </div>

      {schedules.length ? (
        <div className="telegram-table-wrap schedule-table-wrap">
          <table className="telegram-table schedule-table">
            <thead>
              <tr>
                <th>Chat</th>
                <th>Questions</th>
                <th>Time</th>
                <th>Window</th>
                <th>Next run</th>
                <th>Last run</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {schedules.map((schedule) => (
                <tr key={schedule.id} className={!schedule.enabled ? "is-archived" : ""}>
                  <td>
                    <strong>{chatTitle(schedule.telegram_chat_id)}</strong>
                    {schedule.last_error && <span className="table-error">{schedule.last_error}</span>}
                  </td>
                  <td>{questionSetName(schedule.question_set_id)}</td>
                  <td>{schedule.run_time_local} <span className="muted-inline">{schedule.timezone}</span></td>
                  <td>{rollingWindowLabel(schedule.rolling_window_days)}</td>
                  <td>{schedule.enabled ? formatDate(schedule.next_run_at) : "-"}</td>
                  <td>{formatDate(schedule.last_run_at)}</td>
                  <td>
                    <div className="table-actions schedule-actions">
                      <button className="text-button" type="button" onClick={() => editSchedule(schedule)} disabled={busy}>
                        Edit
                      </button>
                      <button className="text-button" type="button" onClick={() => onToggle(schedule)} disabled={busy}>
                        {schedule.enabled ? "Pause" : "Enable"}
                      </button>
                      {schedule.last_job_id && (
                        <button
                          className="text-button"
                          type="button"
                          onClick={() => onOpenJob(schedule.last_job_id)}
                        >
                          Last job
                        </button>
                      )}
                      <button className="text-button" type="button" onClick={() => onDelete(schedule)} disabled={busy}>
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
        <div className="subtle-empty-state">No scheduled reports yet.</div>
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
    await onRefresh();
    showToast("Telegram account disconnected");
  });

  const syncChat = (chatId) => run(async () => {
    await request(`/telegram/chats/${chatId}/sync`, { method: "POST" });
    await onRefresh();
    showToast("Synchronization scheduled");
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
          <span className="page-kicker">Data sources</span>
          <h1>Telegram Setup</h1>
          <p>Connect an account and choose which groups or channels should be collected.</p>
        </div>
      </header>

      {connection?.connected ? (
        <>
          <ConnectedAccount connection={connection} busy={busy} onDisconnect={disconnect} />
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
          <CollectedChatsTable chats={chats} busy={busy} onSync={syncChat} onUpdate={updateChat} />
          <ScheduledReportsSection
            chats={chats}
            questionSets={questionSets}
            schedules={schedules}
            busy={busy}
            onSave={saveSchedule}
            onDelete={deleteSchedule}
            onToggle={toggleSchedule}
            onOpenJob={onSelectJob}
          />
        </>
      ) : (
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
        />
      )}
    </section>
  );
}
