export interface Source {
  file: string;
  text: string;
  score: number;
}

export interface AskResponse {
  answer: string;
  sources: Source[];
  mode: "stub" | "proxy";
}

/** FastAPI sends a string for most errors and a list for validation errors. */
export interface ErrorResponse {
  detail?: unknown;
}
