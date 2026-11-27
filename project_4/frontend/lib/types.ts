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

export type ErrorResponse = {
  detail: string;
};
