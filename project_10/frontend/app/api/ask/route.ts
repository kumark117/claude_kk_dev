import type { ErrorResponse } from "../../../lib/types";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";
const TIMEOUT_MS = 35_000;

function failure(status: number, detail: string): Response {
  return Response.json({ detail } satisfies ErrorResponse, { status });
}

// Forwards the question to FastAPI so the browser only talks to this origin.
export async function POST(request: Request): Promise<Response> {
  const body = await request.text();
  try {
    JSON.parse(body);
  } catch {
    return failure(400, "Invalid JSON");
  }

  const base = (process.env.BACKEND_URL || DEFAULT_BACKEND_URL).replace(
    /\/$/,
    "",
  );
  try {
    const reply = await fetch(`${base}/ask`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    return Response.json(await reply.json(), { status: reply.status });
  } catch {
    return failure(502, "Backend unavailable");
  }
}
