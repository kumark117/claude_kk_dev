import type { ErrorResponse } from "../../../lib/types";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";
const TIMEOUT_MS = 35_000;

function failure(status: number, detail: string): Response {
  const body: ErrorResponse = { detail };
  return Response.json(body, { status });
}

export async function POST(request: Request): Promise<Response> {
  const body = await request.text();
  try {
    JSON.parse(body);
  } catch {
    return failure(400, "Invalid JSON");
  }

  const base = process.env.BACKEND_URL || DEFAULT_BACKEND_URL;
  try {
    const reply = await fetch(`${base.replace(/\/+$/, "")}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    const data: unknown = await reply.json();
    return Response.json(data, { status: reply.status });
  } catch {
    return failure(502, "Backend unavailable");
  }
}
