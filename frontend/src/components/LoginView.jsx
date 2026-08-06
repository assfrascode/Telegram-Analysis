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
      <section className="login-showcase" aria-label="Chat Analysis workspace">
        <div className="login-brand">
          <span className="app-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24"><path d="M5 5.75h14v10.5H9l-4 3z" /><path d="M8 9.25h8M8 12.75h5" /></svg>
          </span>
          <div><strong>Chat Analysis</strong><span>Telegram intelligence workspace</span></div>
        </div>
        <div className="login-illustration" aria-hidden="true">
          <div className="login-orbit login-orbit-one" />
          <div className="login-orbit login-orbit-two" />
          <div className="login-illustration-card login-illustration-card-main">
            <svg viewBox="0 0 24 24"><path d="M5 5.75h14v10.5H9l-4 3z" /><path d="M8 9.25h8M8 12.75h5" /></svg>
          </div>
          <div className="login-illustration-card login-illustration-card-report">
            <svg viewBox="0 0 24 24"><path d="M7 3.75h7.5L19 8.2v12.05H7z" /><path d="M14.5 3.75V8.2H19M10 12h6M10 15.5h6" /></svg>
          </div>
          <span className="login-illustration-status"><span /> Report ready</span>
        </div>
        <div className="login-showcase-copy">
          <span className="page-kicker">Clear answers. Verifiable evidence.</span>
          <h1>Turn busy chats into useful intelligence.</h1>
          <p>Collect Telegram conversations, ask focused questions, and trace each finding back to its source.</p>
        </div>
        <div className="login-feature-row" aria-label="Workspace capabilities">
          <span><i aria-hidden="true">01</i> Collect</span>
          <span><i aria-hidden="true">02</i> Analyse</span>
          <span><i aria-hidden="true">03</i> Report</span>
        </div>
      </section>

      <section className="login-auth-panel">
        <form className="login-card" onSubmit={submit}>
          <div className="login-card-heading">
            <span className="login-security-badge"><span className="status-dot status-dot-completed" /> Secure workspace</span>
            <h2>{isRegistering ? "Create your account" : "Welcome back"}</h2>
            <p className="login-copy">
              {isRegistering ? "Start a private analysis workspace." : "Sign in to continue to your analyses."}
            </p>
          </div>

          <div className="auth-mode-toggle" role="tablist" aria-label="Authentication mode">
            <button
              className={!isRegistering ? "auth-mode-button is-active" : "auth-mode-button"}
              type="button"
              onClick={() => switchMode("login")}
              disabled={busy}
              aria-selected={!isRegistering}
            >
              Sign in
            </button>
            <button
              className={isRegistering ? "auth-mode-button is-active" : "auth-mode-button"}
              type="button"
              onClick={() => switchMode("register")}
              disabled={busy}
              aria-selected={isRegistering}
            >
              Create account
            </button>
          </div>

          <label className="field">
            <span>Email</span>
            <input
              type="email"
              autoComplete="username"
              placeholder="you@example.com"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </label>
          <label className="field">
            <span>Password</span>
            <input
              type="password"
              autoComplete={isRegistering ? "new-password" : "current-password"}
              placeholder="Enter your password"
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
                placeholder="Repeat your password"
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
              />
            </label>
          ) : null}
          {message ? <p className="auth-message auth-message-error" role="alert">{message}</p> : null}
          <button className="button button-primary button-full login-submit" type="submit" disabled={busy}>
            {busy && <span className="button-spinner" aria-hidden="true" />}
            {primaryLabel}
          </button>
          <p className="login-fine-print">Use only accounts and chat data you are authorised to analyse.</p>
        </form>
      </section>
    </main>
  );
}
