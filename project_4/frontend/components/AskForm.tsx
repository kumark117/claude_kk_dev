"use client";

import { type FormEvent, useState } from "react";

import type { AskResponse } from "../lib/types";

type Outcome = { result: AskResponse } | { error: string } | null;

const preserveLines = { whiteSpace: "pre-wrap", overflowWrap: "anywhere" } as const;

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
  const body = await response.json().catch(() => null);
  if (response.ok && body) {
    return { result: body as AskResponse };
  }
  return {
    error:
      typeof body?.detail === "string"
        ? body.detail
        : `Request failed (${response.status})`,
  };
}

export default function AskForm() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const disabled = loading || question.trim() === "";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled) return;
    setLoading(true);
    setOutcome(null);
    setOutcome(await ask(question.trim()));
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

      {outcome && "error" in outcome && (
        <p role="alert" style={{ color: "#b00020" }}>
          {outcome.error}
        </p>
      )}

      {outcome && "result" in outcome && (
        <section>
          <p style={preserveLines}>{outcome.result.answer}</p>
          <h2>Sources</h2>
          {outcome.result.sources.length === 0 ? (
            <p>No matching sources.</p>
          ) : (
            <ol>
              {outcome.result.sources.map((source, index) => (
                <li key={index}>
                  <strong>{source.file}</strong> (score{" "}
                  {source.score.toFixed(2)})
                  <p style={preserveLines}>{source.text}</p>
                </li>
              ))}
            </ol>
          )}
        </section>
      )}
    </>
  );
}
