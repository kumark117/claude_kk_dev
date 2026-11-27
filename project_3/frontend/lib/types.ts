// Shapes of the /ask responses, as produced by the FastAPI backend.

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
