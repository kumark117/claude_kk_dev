export type Source = {
  file: string;
  text: string;
  score: number;
};

/** A successful reply from POST /api/ask. */
export type AskResponse = {
  answer: string;
  sources: Source[];
  mode: "stub" | "proxy";
};

/** An error reply. `detail` is a string, except for FastAPI's 422 list. */
export type ErrorResponse = {
  detail?: unknown;
};
