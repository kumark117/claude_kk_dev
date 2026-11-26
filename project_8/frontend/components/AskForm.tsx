"use client";

import { type FormEvent, useState } from "react";

import type { AskResponse } from "../lib/types";

const preserveLines = { whiteSpace: "pre-wrap" } as const;

export default function AskForm() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const trimmed = question.trim();

  async function submit(event: FormEvent) {
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
      const data = await response.json().catch(() => null);
      if (response.ok && data) {
        setResult(data);
      } else {
        setError(
          typeof data?.detail === "string" ? data.detail : `Request failed (${response.status})`,
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
            style={{ flex: 1, padding: "0.4rem" }}
          />
          <button type="submit" disabled={!trimmed || loading} style={{ padding: "0.4rem 1rem" }}>
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
          <p style={preserveLines}>{result.answer}</p>
          <h2>Sources</h2>
          {result.sources.length === 0 ? (
            <p>No matching sources.</p>
          ) : (
            <ul>
              {result.sources.map((source, index) => (
                <li key={index}>
                  <strong>{source.file}</strong> (score {source.score.toFixed(2)})
                  <p style={preserveLines}>{source.text}</p>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </>
  );
}
