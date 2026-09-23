import { useEffect, useRef, useState } from "react";
import { formatDate, statusLabel } from "../lib/format";
import { WorkspaceTopbar } from "./WorkspaceChrome";

function policyFromDraft(draft) {
  return Object.fromEntries(Object.entries(draft).map(([key, value]) => {
    if (value.trim() === "") return [key, null];
    const days = Number(value);
    if (!Number.isSafeInteger(days) || days < 1 || days > 36500) throw new Error("Retention must be a whole number from 1 to 36500 days, or blank to keep indefinitely.");
    return [key, days];
  }));
}

export function RetentionPanel({ request, showToast, onSelectJob, onRefreshJobs }) {
  const [draft, setDraft] = useState({ job_retention_days: "", upload_retention_days: "" });
  const [saved, setSaved] = useState(null);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const alive = useRef(false);
  const operation = useRef(false);

  const acceptSaved = (data) => {
    setSaved(data);
    setDraft({
      job_retention_days: data.job_retention_days == null ? "" : String(data.job_retention_days),
      upload_retention_days: data.upload_retention_days == null ? "" : String(data.upload_retention_days),
    });
    setPreview(data);
  };

  useEffect(() => {
    alive.current = true;
    let cancelled = false;
    request("/jobs/retention").then((data) => {
      if (!cancelled) acceptSaved(data);
    }).catch((failure) => {
      if (!cancelled && failure.name !== "AbortError") setError(failure.message);
    }).finally(() => {
      if (!cancelled) setBusy(false);
    });
    return () => { cancelled = true; alive.current = false; };
  }, [request]);

  const refresh = async () => {
    if (operation.current) return;
    operation.current = true;
    setBusy(true);
    setError("");
    try {
      const data = await request("/jobs/retention");
      if (alive.current) acceptSaved(data);
      await onRefreshJobs();
    } catch (failure) {
      if (alive.current && failure.name !== "AbortError") setError(failure.message);
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  };

  const previewPolicy = async (event) => {
    event.preventDefault();
    if (operation.current) return;
    setError("");
    let policy;
    try { policy = policyFromDraft(draft); }
    catch (failure) { setError(failure.message); return; }
    operation.current = true;
    setBusy(true);
    setPreview(null);
    try {
      const data = await request("/jobs/retention/preview", { method: "POST", body: policy });
      if (alive.current) setPreview(data);
    } catch (failure) {
      if (alive.current && failure.name !== "AbortError") setError(failure.message);
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  };

  const save = async () => {
    if (!preview || operation.current) return;
    const policy = policyFromDraft(draft);
    const jobCount = preview.job_count ?? preview.jobs.length;
    const uploadCount = preview.upload_count ?? preview.uploads.length;
    if (!window.confirm(`Apply this automatic retention policy? The current preview includes ${jobCount} analyses and ${uploadCount} unused uploads. Eligible data will be permanently deleted. Cleanup already queued will continue even if retention is disabled later.`)) return;
    operation.current = true;
    setBusy(true);
    setError("");
    try {
      const data = await request("/jobs/retention", { method: "PUT", body: policy });
      if (!alive.current) return;
      acceptSaved(data);
      showToast("Retention settings saved. Eligible data is cleaned up automatically.");
      await onRefreshJobs();
    } catch (failure) {
      if (alive.current && failure.name !== "AbortError") setError(failure.message);
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  };

  const edit = (key, value) => {
    setDraft((current) => ({ ...current, [key]: value }));
    setPreview(null);
    setError("");
  };
  const changed = saved && (draft.job_retention_days !== String(saved.job_retention_days ?? "")
    || draft.upload_retention_days !== String(saved.upload_retention_days ?? ""));

  return (
    <section className="workspace-page retention-page">
      <WorkspaceTopbar tone="empty" badge="Storage" title="Storage & retention" subtitle="Manage the lifetime of your analyses and uploads" actions={(
        <button className="button button-secondary button-small" type="button" onClick={refresh} disabled={busy}>Refresh saved policy</button>
      )} />
      <div className="retention-content">
        <section className="panel retention-policy" aria-labelledby="retention-policy-title">
          <span className="section-kicker">Automatic cleanup</span>
          <h1 id="retention-policy-title">Choose how long to keep your data</h1>
          <p>Completed, failed, and cancelled analyses can expire after they finish. Deletion removes their reports, stored artifacts, search indexes, and original uploaded files when no other analysis uses them. Shared collected messages and media still in use are preserved.</p>
          <form onSubmit={previewPolicy}>
            <div className="retention-fields">
              <label htmlFor="job-retention-days">Keep finished analyses for (days)
                <input id="job-retention-days" type="number" min="1" max="36500" step="1" placeholder="Keep indefinitely" value={draft.job_retention_days} onChange={(event) => edit("job_retention_days", event.target.value)} disabled={busy || !saved} aria-describedby="retention-days-help" />
              </label>
              <label htmlFor="upload-retention-days">Keep unused uploaded files for (days)
                <input id="upload-retention-days" type="number" min="1" max="36500" step="1" placeholder="Keep indefinitely" value={draft.upload_retention_days} onChange={(event) => edit("upload_retention_days", event.target.value)} disabled={busy || !saved} aria-describedby="retention-days-help" />
              </label>
            </div>
            <p id="retention-days-help">Leave a field blank to keep that data indefinitely. Uploaded files are eligible only when no analysis uses them; their age is measured from creation.</p>
            {saved && <p>Incomplete or rejected uploads expire automatically after {saved.upload_expiry_hours ?? 24} hours, even when unused-upload retention is disabled.</p>}
            <div className="retention-actions">
              <button className="button button-secondary" type="submit" disabled={busy || !saved}>{busy ? "Loading…" : "Preview cleanup"}</button>
              <button className="button button-primary" type="button" onClick={save} disabled={busy || !preview || !changed}>Save retention policy</button>
            </div>
          </form>
          {error && <p className="danger-text" role="alert">{error}</p>}
        </section>
        <section className="panel retention-preview" aria-labelledby="retention-preview-title" aria-live="polite">
          <span className="section-kicker">Read-only preview</span>
          <h2 id="retention-preview-title">{changed ? "Proposed policy" : "Current policy"}</h2>
          {!preview ? <p>{busy ? "Loading cleanup preview…" : "Preview your changes before saving the policy."}</p> : (
            <>
              <p>{preview.job_count ?? preview.jobs.length} analyses and {preview.upload_count ?? preview.uploads.length} unused uploads are currently eligible. The preview does not delete data. Eligibility is checked again when cleanup runs.</p>
              <p>{preview.pending_deletions} deletions are already queued. Interrupted cleanup resumes automatically.</p>
              {((preview.job_count ?? 0) > preview.jobs.length || (preview.upload_count ?? 0) > preview.uploads.length) && <p>Showing the first {Math.max(preview.jobs.length, preview.uploads.length)} entries in each list. Cleanup applies to all eligible entries.</p>}
              <div className="retention-preview-grid">
                <div>
                  <h3>Analyses</h3>
                  {preview.jobs.length ? <ul className="retention-candidates">{preview.jobs.map((job) => (
                    <li key={job.id}>
                      <button type="button" className="button button-ghost button-small" onClick={() => onSelectJob(job.id)}>{job.source_name || `Analysis ${job.id.slice(0, 8)}`}</button>
                      <span>{statusLabel(job.status)} · Finished {formatDate(job.completed_at)}</span>
                    </li>
                  ))}</ul> : <p>No analyses are eligible now.</p>}
                </div>
                <div>
                  <h3>Unused uploads</h3>
                  {preview.uploads.length ? <ul className="retention-candidates">{preview.uploads.map((upload) => (
                    <li key={upload.id}><strong>{upload.filename}</strong><span>Created {formatDate(upload.created_at)}</span></li>
                  ))}</ul> : <p>No unused uploads are eligible now.</p>}
                </div>
              </div>
            </>
          )}
        </section>
      </div>
    </section>
  );
}
