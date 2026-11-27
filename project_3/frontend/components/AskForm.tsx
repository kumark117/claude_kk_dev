"use client";

import { useState, type FormEvent } from "react";
import type { AskResponse } from "../lib/types";

async function errorMessage(response: Response): Promise<string> {
  const body = await response.json().catch(() => null);
  return typeof body?.detail === "string"
    ? body.detail
    : `Request failed (${response.status})`;
}

export default function AskForm() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const disabled = loading || !question.trim();

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled) return;
    setLoading(true);
    setResult(null);
    setError(null);
    try {
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: question.trim() }),
      });
      if (response.ok) {
        setResult((await response.json()) as AskResponse);
      } else {
        setError(await errorMessage(response));
      }
    } catch {
      setError("Request failed");
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
            autoComplete="off"
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
          <h2>Answer</h2>
          <p className="text">{result.answer}</p>
          <h2>Sources</h2>
          {result.sources.length === 0 ? (
            <p>No matching sources.</p>
          ) : (
            <ul>
              {result.sources.map((source, index) => (
                <li key={index}>
                  <strong>{source.file}</strong>{" "}
                  <span className="score">score {source.score.toFixed(2)}</span>
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
