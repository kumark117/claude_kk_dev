"use client";

import { type FormEvent, useState } from "react";

import type { AskResponse } from "../lib/types";

type State =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "done"; result: AskResponse }
  | { status: "failed"; message: string };

async function ask(question: string): Promise<State> {
  let response: Response;
  try {
    response = await fetch("/api/ask", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question }),
    });
  } catch {
    return { status: "failed", message: "Request failed" };
  }

  const body = await response.json().catch(() => null);
  if (response.ok && body !== null) {
    return { status: "done", result: body as AskResponse };
  }
  const message =
    typeof body?.detail === "string"
      ? body.detail
      : `Request failed (${response.status})`;
  return { status: "failed", message };
}

export default function AskForm() {
  const [question, setQuestion] = useState("");
  const [state, setState] = useState<State>({ status: "idle" });
  const loading = state.status === "loading";
  const blocked = loading || question.trim() === "";

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (blocked) return;
    setState({ status: "loading" });
    setState(await ask(question.trim()));
  }

  return (
    <>
      <form onSubmit={onSubmit}>
        <label htmlFor="question">Question</label>
        <div className="row">
          <input
            id="question"
            type="text"
            maxLength={1000}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
          />
          <button type="submit" disabled={blocked}>
            Ask
          </button>
        </div>
      </form>

      {loading && <p role="status">Thinking…</p>}

      {state.status === "failed" && (
        <p role="alert" className="error">
          {state.message}
        </p>
      )}

      {state.status === "done" && (
        <section>
          <p className="text">{state.result.answer}</p>
          <h2>Sources</h2>
          {state.result.sources.length === 0 ? (
            <p>No matching sources.</p>
          ) : (
            <ul>
              {state.result.sources.map((source, index) => (
                <li key={index}>
                  <strong>{source.file}</strong> (score{" "}
                  {source.score.toFixed(2)})
                  <p className="text">{source.text}</p>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </>
  );
}
