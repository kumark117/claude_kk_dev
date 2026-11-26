/** Shapes returned by the backend's POST /ask, passed through by /api/ask. */
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

export interface ErrorResponse {
  detail: string;
}
