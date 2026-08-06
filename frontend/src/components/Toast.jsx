function ToastIcon({ kind }) {
  if (kind === "error") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M9 9l6 6M15 9l-6 6" /></svg>;
  }
  if (kind === "warning") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5 21 20H3z" /><path d="M12 9v5M12 17.25v.1" /></svg>;
  }
  if (kind === "success") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="m8 12 2.6 2.6L16.5 8.8" /></svg>;
  }
  return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 10.5v6M12 7.5v.1" /></svg>;
}

export function Toast({ toast }) {
  if (!toast?.message) return null;
  const kind = toast.kind || "info";
  const label = kind === "error" ? "Error" : kind === "warning" ? "Warning" : kind === "success" ? "Success" : "Update";
  return (
    <div className={`toast toast-${kind}`} role={kind === "error" ? "alert" : "status"} aria-live={kind === "error" ? "assertive" : "polite"}>
      <span className="toast-icon"><ToastIcon kind={kind} /></span>
      <span className="toast-copy"><strong>{label}</strong><span>{toast.message}</span></span>
      <span className="toast-timer" aria-hidden="true" />
    </div>
  );
}
