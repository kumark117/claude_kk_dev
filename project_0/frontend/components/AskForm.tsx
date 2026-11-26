"use client";

import { type FormEvent, useState } from "react";
import type { AskResponse, ErrorResponse } from "../lib/types";

export default function AskForm() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const trimmed = question.trim();

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!trimmed || loading) return;
    setLoading(true);
    setResult(null);
    setError(null);
    try {
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: trimmed }),
      });
      const body: unknown = await response.json().catch(() => null);
      if (response.ok && body) {
        setResult(body as AskResponse);
      } else {
        const detail = (body as ErrorResponse | null)?.detail;
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
      <form onSubmit={handleSubmit} style={{ display: "flex", gap: "0.5rem" }}>
        <label htmlFor="question">Question</label>
        <input
          id="question"
          type="text"
          maxLength={1000}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          style={{ flex: 1 }}
        />
        <button type="submit" disabled={!trimmed || loading}>
          Ask
        </button>
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
            <ul>
              {result.sources.map((source, index) => (
                <li key={index}>
                  <strong>{source.file}</strong> (score{" "}
                  {source.score.toFixed(2)})
                  <p style={{ whiteSpace: "pre-wrap" }}>{source.text}</p>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </>
  );
}
