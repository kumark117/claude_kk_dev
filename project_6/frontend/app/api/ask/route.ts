import type { ErrorResponse } from "../../../lib/types";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";
const TIMEOUT_MS = 35_000;

function fail(status: number, detail: string): Response {
  return Response.json({ detail } satisfies ErrorResponse, { status });
}

export async function POST(request: Request): Promise<Response> {
  const body = await request.text();
  try {
    JSON.parse(body);
  } catch {
    return fail(400, "Invalid JSON");
  }

  const base = (process.env.BACKEND_URL || DEFAULT_BACKEND_URL).replace(
    /\/+$/,
    "",
  );
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
    return fail(502, "Backend unavailable");
  }
}
