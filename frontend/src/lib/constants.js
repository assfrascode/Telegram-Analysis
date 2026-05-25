export const STORAGE_TOKEN = "chat_analyse_token";
export const STORAGE_JOB = "chat_analyse_current_job";
export const MAX_EVENTS = 300;

export const TERMINAL_STATUSES = new Set(["completed", "cancelled", "failed"]);
export const BAD_STATUSES = new Set(["failed", "cancelled"]);

export const STAGES = [
  { key: "upload", label: "Datei hochladen", events: ["upload.completed"] },
  { key: "validate", label: "Datei prüfen", events: ["zip.scan.started", "zip.scan.completed"] },
  { key: "extract", label: "Chat vorbereiten", events: ["zip.extract.started", "zip.extract.completed"] },
  {
    key: "parse",
    label: "Nachrichten lesen",
    events: ["telegram.parse.started", "telegram.parse.progress", "telegram.parse.completed"],
  },
  {
    key: "media",
    label: "Medien prüfen",
    events: ["media.analysis.started", "media.analysis.progress", "media.analysis.completed"],
  },
  { key: "chunk", label: "Inhalte strukturieren", events: ["chunking.started", "chunking.progress", "chunking.completed"] },
  {
    key: "embedding",
    label: "Suchindex erstellen",
    events: ["embedding.started", "embedding.progress", "embedding.completed"],
  },
  {
    key: "retrieval",
    label: "Relevante Stellen finden",
    events: ["retrieval.started", "retrieval.progress", "retrieval.completed"],
  },
  {
    key: "reranking",
    label: "Treffer sortieren",
    events: ["reranking.started", "reranking.progress", "reranking.completed"],
  },
  {
    key: "answers",
    label: "Fragen beantworten",
    events: ["answer.started", "answer.progress", "answer.completed", "question.answer.completed"],
  },
  { key: "report", label: "Bericht erstellen", events: ["report.started", "report.completed", "job.completed"] },
];

export const DEFAULT_QUESTIONS = [{ id: "q1", text: "Welche Narrative verbreitet der Chat?" }];

export const EXAMPLE_QUESTIONS = [
  { id: "q1", text: "Welche Narrative verbreitet der Chat?" },
  { id: "q2", text: "Welche Personen, Gruppen oder Organisationen werden besonders positiv oder negativ dargestellt?" },
  { id: "q3", text: "Welche Themen oder Aussagen wiederholen sich auffällig häufig?" },
];

export const DEFAULT_OPTIONS = {
  translate: false,
  analyze_media: true,
  retrieval_k: 50,
  rerank_k: 15,
};
