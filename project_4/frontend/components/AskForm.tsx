"use client";

import { type FormEvent, useRef, useState } from "react";

import type { AskResponse, ErrorResponse } from "../lib/types";

async function errorMessage(response: Response): Promise<string> {
  const body: ErrorResponse | null = await response.json().catch(() => null);
  return typeof body?.detail === "string"
    ? body.detail
    : `Request failed (${response.status})`;
}

export default function AskForm() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Set at once, unlike state, so a second submit cannot slip in.
  const inFlight = useRef(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || inFlight.current) return;

    inFlight.current = true;
    setLoading(true);
    setResult(null);
    setError(null);
    try {
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: trimmed }),
      });
      if (response.ok) {
        setResult((await response.json()) as AskResponse);
      } else {
        setError(await errorMessage(response));
      }
    } catch {
      setError("Request failed");
    } finally {
      inFlight.current = false;
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
          <button type="submit" disabled={loading || !question.trim()}>
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
