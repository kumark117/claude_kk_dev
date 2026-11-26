/** Shapes returned by POST /api/ask. */

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
