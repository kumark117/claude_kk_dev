import type { ErrorResponse } from "../../../lib/types";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";
const TIMEOUT_MS = 35_000;

function fail(status: number, detail: string): Response {
  const body: ErrorResponse = { detail };
  return Response.json(body, { status });
}

/** Forward a question to the FastAPI backend, keeping its address server-side. */
export async function POST(request: Request): Promise<Response> {
  let body: string;
  try {
    body = await request.text();
    JSON.parse(body);
  } catch {
    return fail(400, "Invalid JSON");
  }

  const base = (process.env.BACKEND_URL || DEFAULT_BACKEND_URL).replace(/\/+$/, "");
  try {
    const upstream = await fetch(`${base}/ask`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    return Response.json(await upstream.json(), { status: upstream.status });
  } catch {
    return fail(502, "Backend unavailable");
  }
}
