"use client";

import { type FormEvent, useState } from "react";

import type { AskResponse } from "../lib/types";

type Outcome = { result: AskResponse } | { error: string } | null;

async function ask(question: string): Promise<Outcome> {
  let response: Response;
  try {
    response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
  } catch {
    return { error: "Request failed" };
  }
  const data = await response.json().catch(() => undefined);
  if (response.ok && data !== undefined) {
    return { result: data as AskResponse };
  }
  const detail: unknown = data?.detail;
  return {
    error: typeof detail === "string" ? detail : `Request failed (${response.status})`,
  };
}

export default function AskForm() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const trimmed = question.trim();
  const blocked = loading || trimmed === "";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (blocked) return;
    setOutcome(null);
    setLoading(true);
    setOutcome(await ask(trimmed));
    setLoading(false);
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
          <button type="submit" disabled={blocked} style={{ font: "inherit", padding: "0.4rem 1rem" }}>
            Ask
          </button>
        </div>
      </form>

      {loading && <p role="status">Thinking…</p>}

      {outcome && "error" in outcome && (
        <p role="alert" style={{ color: "#b00020" }}>
          {outcome.error}
        </p>
      )}

      {outcome && "result" in outcome && (
        <section>
          <h2>Answer</h2>
          <p style={{ whiteSpace: "pre-wrap" }}>{outcome.result.answer}</p>
          <h2>Sources</h2>
          {outcome.result.sources.length === 0 ? (
            <p>No matching sources.</p>
          ) : (
            <ul>
              {outcome.result.sources.map((source, index) => (
                <li key={index}>
                  <strong>{source.file}</strong> (score {source.score.toFixed(2)})
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
