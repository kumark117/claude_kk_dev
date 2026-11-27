"use client";

import { useState, type CSSProperties, type FormEvent } from "react";

import type { AskResponse } from "../lib/types";

const plainText: CSSProperties = {
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
};

async function ask(question: string): Promise<AskResponse> {
  let response: Response;
  try {
    response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
  } catch {
    throw new Error("Request failed");
  }
  // A body that is not JSON is read as null.
  const body = await response.json().catch(() => null);
  if (response.ok && body !== null) {
    return body as AskResponse;
  }
  throw new Error(
    typeof body?.detail === "string"
      ? body.detail
      : `Request failed (${response.status})`,
  );
}

export default function AskForm() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState("");
  const trimmed = question.trim();
  const blocked = trimmed === "" || loading;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (blocked) return;
    setLoading(true);
    setResult(null);
    setError("");
    try {
      setResult(await ask(trimmed));
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
            disabled={blocked}
            style={{ font: "inherit", padding: "0.4rem 1rem" }}
          >
            Ask
          </button>
        </div>
      </form>

      {loading && <p role="status">Thinking…</p>}

      {error && (
        <p role="alert" style={{ color: "#b00020" }}>
          {error}
        </p>
      )}

      {result && (
        <section>
          <p style={plainText}>{result.answer}</p>
          <h2>Sources</h2>
          {result.sources.length === 0 ? (
            <p>No matching sources.</p>
          ) : (
            <ul>
              {result.sources.map((source, index) => (
                <li key={index}>
                  <strong>{source.file}</strong>{" "}
                  <span>(score {source.score.toFixed(2)})</span>
                  <p style={plainText}>{source.text}</p>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </>
  );
}
