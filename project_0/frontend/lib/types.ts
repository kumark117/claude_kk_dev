export type Source = {
  file: string;
  text: string;
  score: number;
};

export type AskResponse = {
  answer: string;
  sources: Source[];
  mode: "stub" | "proxy";
};

/** An error reply. FastAPI validation errors carry a list in `detail`. */
export type ErrorResponse = {
  detail?: unknown;
};
