"use client";

import { useRef, useState } from "react";

import type { AskResponse } from "../lib/types";

function errorMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null | undefined)?.detail;
  return typeof detail === "string" ? detail : `Request failed (${status})`;
}

const textStyle = { whiteSpace: "pre-wrap", overflowWrap: "anywhere" } as const;

export default function AskForm() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // State updates are not immediate, so a ref guards against double submission.
  const inFlight = useRef(false);

  async function ask(trimmed: string): Promise<void> {
    let response: Response;
    try {
      response = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: trimmed }),
      });
    } catch {
      setError("Request failed");
      return;
    }

    let body: unknown;
    try {
      body = await response.json();
    } catch {
      setError(`Request failed (${response.status})`);
      return;
    }

    if (response.ok) {
      setResult(body as AskResponse);
    } else {
      setError(errorMessage(body, response.status));
    }
  }

  async function handleSubmit(event: { preventDefault(): void }): Promise<void> {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || inFlight.current) return;

    inFlight.current = true;
    setLoading(true);
    setResult(null);
    setError(null);
    try {
      await ask(trimmed);
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }

  return (
    <>
      <form onSubmit={handleSubmit}>
        <label htmlFor="question">Question</label>
        <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.25rem" }}>
          <input
            id="question"
            type="text"
            maxLength={1000}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            style={{ flex: 1, font: "inherit", padding: "0.4rem" }}
          />
          <button
            type="submit"
            disabled={loading || !question.trim()}
            style={{ font: "inherit", padding: "0.4rem 1rem" }}
          >
            Ask
          </button>
        </div>
      </form>

      {loading && <p role="status">Thinking…</p>}
      {error !== null && (
        <p role="alert" style={{ color: "#b00020" }}>
          {error}
        </p>
      )}
      {result && (
        <>
          <h2>Answer</h2>
          <p style={textStyle}>{result.answer}</p>
          <h2>Sources</h2>
          {result.sources.length === 0 ? (
            <p>No matching sources.</p>
          ) : (
            <ul>
              {result.sources.map((source, index) => (
                <li key={index}>
                  <strong>{source.file}</strong> (score {source.score.toFixed(2)})
                  <p style={textStyle}>{source.text}</p>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </>
  );
}
