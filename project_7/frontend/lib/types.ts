export type Mode = "stub" | "proxy";

export interface Source {
  file: string;
  text: string;
  score: number;
}

export interface AskResponse {
  answer: string;
  sources: Source[];
  mode: Mode;
}

export interface ErrorResponse {
  detail: string;
}
