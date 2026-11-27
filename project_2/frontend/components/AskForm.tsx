"use client";

import { type FormEvent, useState } from "react";
import type { AskResponse } from "../lib/types";

type State =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "done"; result: AskResponse }
  | { status: "failed"; message: string };

async function ask(question: string): Promise<State> {
  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question }),
    });
    // A body that is not JSON is reported by its status below.
    const body = await response.json().catch(() => null);
    if (response.ok && Array.isArray(body?.sources)) {
      return { status: "done", result: body };
    }
    const message =
      typeof body?.detail === "string"
        ? body.detail
        : `Request failed (${response.status})`;
    return { status: "failed", message };
  } catch {
    return { status: "failed", message: "Request failed" };
  }
}

export default function AskForm() {
  const [question, setQuestion] = useState("");
  const [state, setState] = useState<State>({ status: "idle" });
  const loading = state.status === "loading";
  const blocked = loading || question.trim() === "";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (blocked) return;
    setState({ status: "loading" });
    setState(await ask(question.trim()));
  }

  return (
    <>
      <form onSubmit={submit}>
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
      {state.status === "done" && <Answer result={state.result} />}
    </>
  );
}

function Answer({ result }: { result: AskResponse }) {
  return (
    <section>
      <p className="text">{result.answer}</p>
      <h2>Sources</h2>
      {result.sources.length === 0 ? (
        <p>No matching sources.</p>
      ) : (
        <ul>
          {result.sources.map((source, index) => (
            <li key={index}>
              <strong>{source.file}</strong>{" "}
              <span>(score {source.score.toFixed(2)})</span>
              <p className="text">{source.text}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
