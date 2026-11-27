import type { ErrorResponse } from "../../../lib/types";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";
const TIMEOUT_MS = 35_000;

function error(status: number, detail: string): Response {
  const body: ErrorResponse = { detail };
  return Response.json(body, { status });
}

export async function POST(request: Request): Promise<Response> {
  let body: string;
  try {
    body = await request.text();
    JSON.parse(body);
  } catch {
    return error(400, "Invalid JSON");
  }

  const base = process.env.BACKEND_URL || DEFAULT_BACKEND_URL;
  try {
    const reply = await fetch(`${base.replace(/\/$/, "")}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    const text = await reply.text();
    JSON.parse(text); // throws on a non-JSON reply
    return new Response(text, {
      status: reply.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch {
    return error(502, "Backend unavailable");
  }
}
