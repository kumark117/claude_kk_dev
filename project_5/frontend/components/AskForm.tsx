"use client";

import { type FormEvent, useState } from "react";

import type { AskResponse } from "../lib/types";

const MAX_QUESTION_LENGTH = 1000;
const plainText = { whiteSpace: "pre-wrap", overflowWrap: "anywhere" } as const;

async function errorMessage(response: Response): Promise<string> {
  const body = await response.json().catch(() => null);
  return typeof body?.detail === "string"
    ? body.detail
    : `Request failed (${response.status})`;
}

export function AskForm() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const disabled = loading || !question.trim();

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
      <form onSubmit={submit}>
        <label htmlFor="question">Question</label>
        <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.25rem" }}>
          <input
            id="question"
            type="text"
            maxLength={MAX_QUESTION_LENGTH}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            style={{ flex: 1, padding: "0.5rem", font: "inherit" }}
          />
          <button
            type="submit"
            disabled={disabled}
            style={{ padding: "0.5rem 1rem", font: "inherit" }}
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
        <section>
          <h2>Answer</h2>
          <p style={plainText}>{result.answer}</p>
          <h2>Sources</h2>
          {result.sources.length === 0 ? (
            <p>No matching sources.</p>
          ) : (
            <ol>
              {result.sources.map((source, index) => (
                <li key={index}>
                  <strong>{source.file}</strong> (score{" "}
                  {source.score.toFixed(2)})
                  <p style={plainText}>{source.text}</p>
                </li>
              ))}
            </ol>
          )}
        </section>
      )}
    </>
  );
}
