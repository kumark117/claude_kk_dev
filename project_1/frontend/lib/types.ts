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

// A 422 from the backend carries a list in detail, so its type is unknown.
export type ErrorResponse = {
  detail?: unknown;
};
