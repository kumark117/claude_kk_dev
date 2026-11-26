import type { ErrorResponse } from "../../../lib/types";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";
const TIMEOUT_MS = 35_000;

function failure(detail: string, status: number): Response {
  return Response.json({ detail } satisfies ErrorResponse, { status });
}

/** Forwards the question to the FastAPI backend. */
export async function POST(request: Request): Promise<Response> {
  let body: string;
  try {
    body = await request.text();
    JSON.parse(body);
  } catch {
    return failure("Invalid JSON", 400);
  }

  const base = process.env.BACKEND_URL || DEFAULT_BACKEND_URL;
  try {
    const reply = await fetch(`${base.replace(/\/$/, "")}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    return Response.json(await reply.json(), { status: reply.status });
  } catch {
    return failure("Backend unavailable", 502);
  }
}
