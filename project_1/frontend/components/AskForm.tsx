"use client";

import { type FormEvent, useState } from "react";

import type { AskResponse, ErrorResponse } from "../lib/types";

export default function AskForm() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const disabled = loading || !question.trim();

  async function ask(event: FormEvent) {
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
      const data: unknown = await response.json().catch(() => null);
      if (response.ok && data) {
        setResult(data as AskResponse);
      } else {
        const detail = (data as ErrorResponse | null)?.detail;
        setError(
          typeof detail === "string"
            ? detail
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
      <form onSubmit={ask}>
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

      <div aria-live="polite">
        {loading && <p>Thinking…</p>}
        {error !== null && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        {result && (
          <>
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
                    <span className="score">
                      score {source.score.toFixed(2)}
                    </span>
                    <p className="text">{source.text}</p>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </>
  );
}
