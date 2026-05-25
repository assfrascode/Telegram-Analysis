export function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
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
    throw new Error("Bitte mindestens eine Frage eintragen.");
  }

  return questions.map((question, index) => {
    const text = String(question?.text || "").trim();
    if (!text) throw new Error(`Frage ${index + 1} ist leer.`);
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
    throw new Error("Interne Einstellung ungültig: Retrieval-K muss zwischen 1 und 200 liegen.");
  }
  if (!Number.isInteger(rerankK) || rerankK < 1 || rerankK > 100) {
    throw new Error("Interne Einstellung ungültig: Rerank-K muss zwischen 1 und 100 liegen.");
  }
  if (rerankK > retrievalK) {
    throw new Error("Interne Einstellung ungültig: Rerank-K darf nicht größer als Retrieval-K sein.");
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
    queued: "Wartet",
    running: "Läuft",
    completed: "Fertig",
    failed: "Fehler",
    cancelled: "Abgebrochen",
    cancelling: "Wird abgebrochen",
    pending: "Wartet",
    uploaded: "Hochgeladen",
    ok: "OK",
  };
  return labels[status] || status || "-";
}

export function formatProgressPayload(payload = {}) {
  if (!payload || typeof payload !== "object") return "";
  const done = payload.done ?? payload.completed ?? payload.media_done ?? payload.questions_done ?? payload.chunks_done;
  const total = payload.total ?? payload.media_total ?? payload.questions_total ?? payload.chunks_total;
  if (done !== undefined && total !== undefined) return `${done}/${total}`;
  if (payload.progress !== undefined) return `${payload.progress}%`;
  return "";
}
