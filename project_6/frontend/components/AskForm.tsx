"use client";

import { type FormEvent, useState } from "react";
import type { AskResponse } from "../lib/types";

export default function AskForm() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const disabled = loading || question.trim() === "";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled) return;
    setLoading(true);
    setResult(null);
    setError(null);
    try {
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: question.trim() }),
      });
      const body = await response.json().catch(() => null);
      if (response.ok && body) {
        setResult(body as AskResponse);
      } else {
        setError(
          typeof body?.detail === "string"
            ? body.detail
            : `Request failed (${response.status})`,
        );
      }
    } catch {
      setError("Request failed");
    } finally {
      setLoading(false);
    }
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
