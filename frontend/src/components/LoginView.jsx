import { useState } from "react";

export function LoginView({ onLogin, onRegister, busy }) {
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [message, setMessage] = useState("");

  const isRegistering = mode === "register";

  const switchMode = (nextMode) => {
    setMode(nextMode);
    setPassword("");
    setConfirmPassword("");
    setMessage("");
  };

  const submit = (event) => {
    event.preventDefault();
    setMessage("");

    if (isRegistering) {
      if (password !== confirmPassword) {
        setMessage("Passwords do not match.");
        return;
      }
      onRegister({ email: email.trim(), password });
      return;
    }

    onLogin({ email: email.trim(), password });
  };

  const primaryLabel = busy
    ? isRegistering
      ? "Creating account..."
      : "Signing in..."
    : isRegistering
      ? "Create account"
      : "Sign in";

  return (
    <main className="login-view">
      <form className="login-card" onSubmit={submit}>
        <div className="mark-row">
          <span className="app-mark">CA</span>
          <span className="eyebrow">Chat Analysis</span>
        </div>
        <div className="auth-mode-toggle" role="tablist" aria-label="Authentication mode">
          <button
            className={!isRegistering ? "auth-mode-button is-active" : "auth-mode-button"}
            type="button"
            onClick={() => switchMode("login")}
            disabled={busy}
          >
            Sign in
          </button>
          <button
            className={isRegistering ? "auth-mode-button is-active" : "auth-mode-button"}
            type="button"
            onClick={() => switchMode("register")}
            disabled={busy}
          >
            Create account
          </button>
        </div>
        <h1>{isRegistering ? "Create account" : "Intelligence workspace"}</h1>
        <p className="login-copy">
          {isRegistering
            ? "Create an account to analyse Telegram exports and collected chats."
            : "Sign in to analyse Telegram exports and collected chats."}
        </p>

        <label className="field">
          <span>Email</span>
          <input autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            autoComplete={isRegistering ? "new-password" : "current-password"}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {isRegistering ? (
          <label className="field">
            <span>Confirm password</span>
            <input
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
            />
          </label>
        ) : null}
        {message ? <p className="auth-message auth-message-error">{message}</p> : null}
        <button className="button button-primary button-full" type="submit" disabled={busy}>
          {primaryLabel}
        </button>
      </form>
    </main>
  );
}
