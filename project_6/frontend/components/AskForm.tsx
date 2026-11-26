"use client";

import { type FormEvent, useState } from "react";

import type { AskResponse } from "../lib/types";

/** Returns the answer, or throws an Error whose message can be shown. */
async function ask(question: string): Promise<AskResponse> {
  let reply: Response;
  try {
    reply = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
  } catch {
    throw new Error("Request failed");
  }

  const body = await reply.json().catch(() => null);
  if (reply.ok && body !== null) {
    return body as AskResponse;
  }
  throw new Error(
    typeof body?.detail === "string"
      ? body.detail
      : `Request failed (${reply.status})`,
  );
}

export default function AskForm() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const disabled = loading || !question.trim();

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (disabled) return;
    setLoading(true);
    setResult(null);
    setError(null);
    try {
      setResult(await ask(question.trim()));
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <form onSubmit={handleSubmit}>
        <label htmlFor="question">Question</label>
        <div className="row">
          <input
            id="question"
            type="text"
            maxLength={1000}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
          />
          <button type="submit" disabled={disabled}>
            Ask
          </button>
        </div>
      </form>

      {loading && <p role="status">Thinking…</p>}
      {error !== null && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {result && (
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
      )}
    </>
  );
}
