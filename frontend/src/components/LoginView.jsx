import { useState } from "react";

export function LoginView({ onLogin, busy }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const submit = (event) => {
    event.preventDefault();
    onLogin({ email: email.trim(), password });
  };

  return (
    <main className="login-view">
      <form className="login-card" onSubmit={submit}>
        <div className="mark-row">
          <span className="app-mark">CA</span>
          <span className="eyebrow">Chat Analyse</span>
        </div>
        <h1>Analyseplattform</h1>
        <p className="login-copy">Melden Sie sich an, um Telegram-Exporte hochzuladen, Fragen zu definieren und Berichte herunterzuladen.</p>

        <label className="field">
          <span>E-Mail</span>
          <input autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} />
        </label>
        <label className="field">
          <span>Passwort</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        <button className="button button-primary button-full" type="submit" disabled={busy}>
          Anmelden
        </button>
      </form>
    </main>
  );
}
