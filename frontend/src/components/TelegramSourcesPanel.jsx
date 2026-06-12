import { useState } from "react";
import { formatDate } from "../lib/format";

function chatStatusText(status) {
  if (status === "syncing") return "Syncing";
  if (status === "error") return "Sync failed";
  if (status === "archived") return "Archived";
  return "Active";
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

export function TelegramSourcesPanel({ connection, chats, request, onRefresh, showToast }) {
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
    } catch (error) {
      showToast(error.message, "error");
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
