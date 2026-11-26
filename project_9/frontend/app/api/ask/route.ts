import type { ErrorResponse } from "../../../lib/types";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";
const TIMEOUT_MS = 35_000;

function failure(status: number, detail: string): Response {
  const body: ErrorResponse = { detail };
  return Response.json(body, { status });
}

/** Forward a question to the FastAPI server and relay its reply. */
export async function POST(request: Request): Promise<Response> {
  let body: string;
  try {
    body = await request.text();
    JSON.parse(body);
  } catch {
    return failure(400, "Invalid JSON");
  }

  const base = (process.env.BACKEND_URL || DEFAULT_BACKEND_URL).replace(/\/+$/, "");
  try {
    const reply = await fetch(`${base}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      cache: "no-store",
      // Also covers reading the reply body below.
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    const data: unknown = await reply.json();
    return Response.json(data, { status: reply.status });
  } catch {
    return failure(502, "Backend unavailable");
  }
}
