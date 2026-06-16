export function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

export function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / Math.pow(1024, index)).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export function shortId(id) {
  return String(id || "").slice(0, 8);
}

export function normalizeEvent(event) {
  return {
    id: event?.id || 0,
    event_type: event?.event_type || event?.type || "event",
    level: event?.level || "info",
    message: event?.message || "",
    payload: event?.payload || {},
    created_at: event?.created_at || new Date().toISOString(),
  };
}

export function normalizeQuestions(questions) {
  if (!Array.isArray(questions) || questions.length === 0) {
    throw new Error("Add at least one question.");
  }

  return questions.map((question, index) => {
    const text = String(question?.text || "").trim();
    if (!text) throw new Error(`Question ${index + 1} is empty.`);
    return {
      id: String(question?.id || `q${index + 1}`).trim() || `q${index + 1}`,
      text,
    };
  });
}

export function optionsFromState(options) {
  const retrievalK = Number(options.retrieval_k ?? 50);
  const rerankK = Number(options.rerank_k ?? 15);

  if (!Number.isInteger(retrievalK) || retrievalK < 1 || retrievalK > 200) {
    throw new Error("Invalid internal setting: Retrieval K must be between 1 and 200.");
  }
  if (!Number.isInteger(rerankK) || rerankK < 1 || rerankK > 100) {
    throw new Error("Invalid internal setting: Rerank K must be between 1 and 100.");
  }
  if (rerankK > retrievalK) {
    throw new Error("Invalid internal setting: Rerank K cannot exceed Retrieval K.");
  }

  return {
    translate: Boolean(options.translate),
    analyze_media: options.analyze_media !== false,
    retrieval_k: retrievalK,
    rerank_k: rerankK,
  };
}

export function badgeClassForStatus(status) {
  if (["completed", "uploaded", "ok"].includes(status)) return "badge badge-success";
  if (["failed", "rejected", "error", "cancelled"].includes(status)) return "badge badge-error";
  if (["running", "queued", "cancelling"].includes(status)) return "badge badge-warning";
  return "badge badge-muted";
}

export function statusLabel(status) {
  const labels = {
    queued: "Queued",
    running: "Running",
    completed: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
    cancelling: "Cancelling",
    pending: "Pending",
    uploaded: "Uploaded",
    ok: "OK",
  };
  return labels[status] || status || "-";
}

export function formatProgressPayload(payload = {}) {
  if (!payload || typeof payload !== "object") return "";
  const done = payload.done
    ?? payload.completed
    ?? payload.media_done
    ?? payload.questions_done
    ?? payload.chunks_done
    ?? payload.texts_done;
  const total = payload.total
    ?? payload.media_total
    ?? payload.questions_total
    ?? payload.chunks_total
    ?? payload.texts_total;
  if (done !== undefined && total !== undefined) return `${done}/${total}`;
  if (payload.progress !== undefined) return `${payload.progress}%`;
  return "";
}
