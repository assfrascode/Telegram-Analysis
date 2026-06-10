export function Topbar({ isLoggedIn, onLogout, onNewJob }) {
  return (
    <header className="topbar">
      <div className="brand">
        <span className="app-mark">CA</span>
        <div>
          <strong>Chat Analyse</strong>
          <span>Telegram-Chats sammeln und auswerten</span>
        </div>
      </div>
      <div className="top-actions">
        <button className="button button-primary button-small" type="button" onClick={onNewJob}>
          Neue Analyse starten
        </button>
        <span className={isLoggedIn ? "badge badge-success" : "badge badge-muted"}>{isLoggedIn ? "Angemeldet" : "Nicht angemeldet"}</span>
        <button className="button button-ghost button-small" type="button" onClick={onLogout}>
          Abmelden
        </button>
      </div>
    </header>
  );
}
