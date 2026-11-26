import type { ErrorResponse } from "../../../lib/types";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";
const TIMEOUT_MS = 35_000;

function failure(status: number, detail: string) {
  return Response.json({ detail } satisfies ErrorResponse, { status });
}

export async function POST(request: Request) {
  const body = await request.text();
  try {
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
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    return Response.json(await reply.json(), { status: reply.status });
  } catch {
    return failure(502, "Backend unavailable");
  }
}
