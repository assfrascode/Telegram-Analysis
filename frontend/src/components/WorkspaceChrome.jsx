function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="m7.5 12 3 3 6-7" />
      <circle cx="12" cy="12" r="9" />
    </svg>
  );
}

export function WorkspaceTopbar({
  tone,
  badge,
  title,
  subtitle,
  meta,
  actions,
  className = "",
}) {
  return (
    <header className={`workspace-topbar ${className}`.trim()}>
      <div className="workspace-topbar-copy">
        <span className={`workspace-state-badge workspace-state-badge-${tone}`}>
          <span className="status-dot" />
          {badge}
        </span>
        <div className="workspace-topbar-title">
          <h2>{title}</h2>
          <span>{subtitle}</span>
        </div>
        {meta}
      </div>
      <div className="workspace-topbar-actions">{actions}</div>
    </header>
  );
}

export function WorkspaceRail({
  items,
  ariaLabel,
  value,
  max,
  valueText,
  className = "",
}) {
  const progressProps = value !== undefined && max !== undefined
    ? {
        role: "progressbar",
        "aria-valuemin": 0,
        "aria-valuemax": max,
        "aria-valuenow": value,
        "aria-valuetext": valueText,
      }
    : { role: "group" };

  return (
    <section className={`workspace-rail ${className}`.trim()} aria-label={ariaLabel} {...progressProps}>
      <div className="workspace-rail-grid" style={{ "--workspace-rail-items": items.length }}>
        {items.map((item, index) => (
          <div
            className={`workspace-rail-item workspace-rail-item-${item.status}`}
            key={item.key}
            aria-current={item.status === "running" || item.status === "active" ? "step" : undefined}
          >
            <div className="workspace-rail-track">
              <span className="workspace-rail-fill" style={{ width: `${item.progress ?? 0}%` }} />
              {(item.status === "running" || item.status === "working") && <span className="workspace-rail-shimmer" aria-hidden="true" />}
            </div>
            <div className="workspace-rail-label">
              <span className="workspace-rail-marker" aria-hidden="true">
                {item.status === "completed" || item.status === "configured"
                  ? <CheckIcon />
                  : item.status === "failed" ? "!" : index + 1}
              </span>
              <span><strong>{item.label}</strong><small>{item.detail}</small></span>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
