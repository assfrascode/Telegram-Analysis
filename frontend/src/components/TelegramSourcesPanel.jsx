import { useState } from "react";

function statusText(connection) {
  if (!connection?.connected) return "Nicht verbunden";
  return connection.display_name || connection.phone || "Verbunden";
}

function chatStatusText(chat) {
  if (chat.status === "syncing") return "Synchronisierung läuft";
  if (chat.status === "error") return "Synchronisierung fehlgeschlagen";
  if (chat.status === "archived") return "Archiviert";
  return "Aktiv";
}

export function TelegramSourcesPanel({
  connection,
  chats,
  request,
  onRefresh,
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
    showToast("Telegram-Code wurde angefordert");
  });

  const verifyCode = () => run(async () => {
    const result = await request("/telegram/connection/code", {
      method: "POST",
      body: { challenge_id: challengeId, code },
    });
    if (result.requires_password) {
      setRequiresPassword(true);
      showToast("Telegram verlangt das Zwei-Schritt-Passwort");
      return;
    }
    setChallengeId(null);
    setCode("");
    await onRefresh();
    showToast("Telegram-Konto verbunden");
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
    showToast("Telegram-Konto verbunden");
  });

  const loadDialogs = () => run(async () => {
    const result = await request("/telegram/dialogs");
    setDialogs(result);
    if (result.length) setSelectedDialogId(String(result[0].telegram_chat_id));
  });

  const addChat = () => run(async () => {
    const dialog = dialogs.find((item) => String(item.telegram_chat_id) === selectedDialogId);
    if (!dialog) throw new Error("Bitte eine Gruppe oder einen Kanal auswählen");
    await request("/telegram/chats", {
      method: "POST",
      body: {
        ...dialog,
        initial_sync_from: new Date(`${initialSyncFrom}T00:00:00`).toISOString(),
        sync_interval_minutes: Number(interval),
      },
    });
    await onRefresh();
    showToast("Chat wurde zur Sammlung hinzugefügt");
  });

  const disconnect = () => run(async () => {
    if (!window.confirm("Telegram-Sitzung wirklich widerrufen und trennen?")) return;
    await request("/telegram/connection", { method: "DELETE" });
    setDialogs([]);
    await onRefresh();
    showToast("Telegram-Konto getrennt");
  });

  const syncChat = (chatId) => run(async () => {
    await request(`/telegram/chats/${chatId}/sync`, { method: "POST" });
    await onRefresh();
    showToast("Synchronisierung wurde eingeplant");
  });

  const updateChat = (chatId, body) => run(async () => {
    await request(`/telegram/chats/${chatId}`, { method: "PATCH", body });
    await onRefresh();
    showToast(body.archived ? "Chat archiviert" : "Chat-Einstellungen aktualisiert");
  });

  return (
    <section className="telegram-sources-card">
      <div className="split">
        <div>
          <span className="section-kicker">Telegram-Zugang</span>
          <h2>{statusText(connection)}</h2>
          <p className="compact-hint">
            Verwenden Sie API-ID und API-Hash Ihrer eigenen Anwendung von my.telegram.org.
          </p>
        </div>
        {connection?.connected && (
          <button className="button button-danger button-small" type="button" onClick={disconnect} disabled={busy}>
            Verbindung trennen
          </button>
        )}
      </div>

      {!connection?.connected && !challengeId && (
        <div className="telegram-form-grid">
          <label className="field">
            <span>API-ID</span>
            <input value={apiId} inputMode="numeric" onChange={(event) => setApiId(event.target.value)} />
          </label>
          <label className="field">
            <span>API-Hash</span>
            <input value={apiHash} type="password" onChange={(event) => setApiHash(event.target.value)} />
          </label>
          <label className="field">
            <span>Telefonnummer</span>
            <input value={phone} placeholder="+49…" onChange={(event) => setPhone(event.target.value)} />
          </label>
          <button className="button button-primary" type="button" onClick={startLogin} disabled={busy || !apiId || !apiHash || !phone}>
            Code anfordern
          </button>
        </div>
      )}

      {challengeId && !requiresPassword && (
        <div className="inline-actions telegram-code-row">
          <label className="field">
            <span>Telegram-Code</span>
            <input value={code} autoComplete="one-time-code" onChange={(event) => setCode(event.target.value)} />
          </label>
          <button className="button button-primary" type="button" onClick={verifyCode} disabled={busy || !code}>
            Code bestätigen
          </button>
        </div>
      )}

      {challengeId && requiresPassword && (
        <div className="inline-actions telegram-code-row">
          <label className="field">
            <span>Zwei-Schritt-Passwort</span>
            <input value={password} type="password" onChange={(event) => setPassword(event.target.value)} />
          </label>
          <button className="button button-primary" type="button" onClick={verifyPassword} disabled={busy || !password}>
            Passwort bestätigen
          </button>
        </div>
      )}

      {connection?.connected && (
        <>
          <div className="inline-actions telegram-dialog-actions">
            <button className="button button-secondary" type="button" onClick={loadDialogs} disabled={busy}>
              Gruppen und Kanäle laden
            </button>
          </div>
          {dialogs.length > 0 && (
            <div className="telegram-form-grid">
              <label className="field">
                <span>Chat</span>
                <select value={selectedDialogId} onChange={(event) => setSelectedDialogId(event.target.value)}>
                  {dialogs.map((dialog) => (
                    <option key={`${dialog.chat_type}-${dialog.telegram_chat_id}`} value={dialog.telegram_chat_id}>
                      {dialog.title} ({dialog.chat_type})
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Erste Sammlung ab</span>
                <input type="date" value={initialSyncFrom} onChange={(event) => setInitialSyncFrom(event.target.value)} />
              </label>
              <label className="field">
                <span>Intervall</span>
                <select value={interval} onChange={(event) => setInterval(Number(event.target.value))}>
                  <option value={15}>15 Minuten</option>
                  <option value={60}>Stündlich</option>
                  <option value={360}>Alle 6 Stunden</option>
                  <option value={1440}>Täglich</option>
                </select>
              </label>
              <button className="button button-primary" type="button" onClick={addChat} disabled={busy}>
                Chat hinzufügen
              </button>
            </div>
          )}
          <div className="telegram-chat-list">
            {chats.map((chat) => (
              <article className="telegram-chat-row" key={chat.id}>
                <div>
                  <strong>{chat.title}</strong>
                  <p className="compact-hint">
                    {chatStatusText(chat)} · zuletzt {chat.last_sync_at ? new Date(chat.last_sync_at).toLocaleString("de-DE") : "noch nie"}
                  </p>
                  {chat.status !== "syncing" && (
                    <p className="compact-hint">
                      Nächster Versuch: {new Date(chat.next_sync_at).toLocaleString("de-DE")}
                    </p>
                  )}
                  {chat.last_error && <p className="compact-hint error-text">{chat.last_error}</p>}
                </div>
                <div className="telegram-chat-controls">
                  <select
                    className="select-small"
                    value={chat.sync_interval_minutes}
                    onChange={(event) => updateChat(chat.id, { sync_interval_minutes: Number(event.target.value) })}
                    disabled={busy || chat.status === "archived"}
                  >
                    <option value={15}>15 Min.</option>
                    <option value={60}>Stündlich</option>
                    <option value={360}>6 Stunden</option>
                    <option value={1440}>Täglich</option>
                  </select>
                  <button className="button button-small button-secondary" type="button" onClick={() => syncChat(chat.id)} disabled={busy || chat.status === "archived"}>
                    Jetzt synchronisieren
                  </button>
                  <button className="button button-small button-ghost" type="button" onClick={() => updateChat(chat.id, { archived: chat.status !== "archived" })} disabled={busy}>
                    {chat.status === "archived" ? "Reaktivieren" : "Archivieren"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
