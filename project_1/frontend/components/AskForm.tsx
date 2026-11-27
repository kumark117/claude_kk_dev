"use client";

import { type FormEvent, useState } from "react";
import type { AskResponse, ErrorResponse } from "../lib/types";

export default function AskForm() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const blocked = loading || question.trim() === "";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (blocked) return;
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
        const body: ErrorResponse | null = await response
          .json()
          .catch(() => null);
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
        <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.25rem" }}>
          <input
            id="question"
            type="text"
            maxLength={1000}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            style={{ flex: 1, padding: "0.5rem", font: "inherit" }}
          />
          <button
            type="submit"
            disabled={blocked}
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
          <p style={{ whiteSpace: "pre-wrap" }}>{result.answer}</p>
          <h2>Sources</h2>
          {result.sources.length === 0 ? (
            <p>No matching sources.</p>
          ) : (
            <ol>
              {result.sources.map((source, index) => (
                <li key={index}>
                  <strong>{source.file}</strong> (score{" "}
                  {source.score.toFixed(2)})
                  <p style={{ whiteSpace: "pre-wrap" }}>{source.text}</p>
                </li>
              ))}
            </ol>
          )}
        </section>
      )}
    </>
  );
}
