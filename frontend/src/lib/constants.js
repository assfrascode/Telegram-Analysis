export const STORAGE_TOKEN = "chat_analyse_token";
export const STORAGE_JOB = "chat_analyse_current_job";
export const MAX_EVENTS = 300;

export const TERMINAL_STATUSES = new Set(["completed", "cancelled", "failed"]);
export const BAD_STATUSES = new Set(["failed", "cancelled"]);

export const STAGES = [
  {
    key: "telegram_sync",
    label: "Synchronize Telegram",
    events: ["telegram.sync.started", "telegram.sync.completed", "telegram.snapshot.completed"],
  },
  { key: "upload", label: "Upload file", events: ["upload.completed"] },
  { key: "validate", label: "Validate file", events: ["zip.scan.started", "zip.scan.completed"] },
  { key: "extract", label: "Prepare chat", events: ["zip.extract.started", "zip.extract.completed"] },
  {
    key: "parse",
    label: "Read messages",
    events: ["telegram.parse.started", "telegram.parse.progress", "telegram.parse.completed"],
  },
  {
    key: "translation",
    label: "Translate messages",
    events: [
      "translation.started",
      "translation.progress",
      "translation.completed",
      "translation.failed",
    ],
  },
  {
    key: "media",
    label: "Analyse media",
    events: ["media.analysis.started", "media.analysis.progress", "media.analysis.completed"],
  },
  { key: "chunk", label: "Structure content", events: ["chunking.started", "chunking.progress", "chunking.completed"] },
  {
    key: "embedding",
    label: "Build search index",
    events: ["embedding.started", "embedding.progress", "embedding.completed"],
  },
  {
    key: "retrieval",
    label: "Find relevant passages",
    events: ["retrieval.started", "retrieval.progress", "retrieval.completed"],
  },
  {
    key: "reranking",
    label: "Rank results",
    events: ["reranking.started", "reranking.progress", "reranking.completed"],
  },
  {
    key: "answers",
    label: "Answer questions",
    events: ["answer.started", "answer.progress", "answer.completed", "question.answer.completed"],
  },
  { key: "report", label: "Create report", events: ["report.started", "report.completed", "job.completed"] },
];

export const DEFAULT_QUESTIONS = [{ id: "q1", text: "What narratives are being promoted in this chat?" }];

export const EXAMPLE_QUESTIONS = [
  { id: "q1", text: "What narratives are being promoted in this chat?" },
  { id: "q2", text: "Which people, groups, or organisations are portrayed especially positively or negatively?" },
  { id: "q3", text: "Which topics or claims are repeated unusually often?" },
];

export const DEFAULT_OPTIONS = {
  translate: false,
  analyze_media: true,
  allow_partial_telegram_sync: false,
  retrieval_k: 50,
  rerank_k: 15,
};
