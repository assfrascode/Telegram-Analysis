export function Toast({ toast }) {
  if (!toast?.message) return null;
  return <div className={`toast toast-${toast.kind || "info"}`}>{toast.message}</div>;
}
