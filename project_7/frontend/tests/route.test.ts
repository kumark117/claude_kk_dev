import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "../app/api/ask/route";

const fetchMock = vi.fn<typeof fetch>();

function ask(body: string = JSON.stringify({ question: "cats" })) {
  return POST(
    new Request("http://127.0.0.1/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    }),
  );
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("BACKEND_URL", "http://127.0.0.1:9999");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("POST /api/ask", () => {
  it.each(["not json", "", "{"])("rejects invalid JSON %j with 400", async (body) => {
    const response = await ask(body);

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ detail: "Invalid JSON" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the body to <BACKEND_URL>/ask", async () => {
    fetchMock.mockResolvedValue(Response.json({}));

    await ask(JSON.stringify({ question: "cats" }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:9999/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    expect(JSON.parse(init?.body as string)).toEqual({ question: "cats" });
  });

  it("reads BACKEND_URL on every request and tolerates a trailing slash", async () => {
    fetchMock.mockImplementation(async () => Response.json({}));

    await ask();
    vi.stubEnv("BACKEND_URL", "http://127.0.0.1:7777/");
    await ask();

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "http://127.0.0.1:9999/ask",
      "http://127.0.0.1:7777/ask",
    ]);
  });

  it.each([undefined, ""])("defaults to port 8000 when BACKEND_URL is %j", async (value) => {
    vi.stubEnv("BACKEND_URL", value);
    fetchMock.mockResolvedValue(Response.json({}));

    await ask();

    expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:8000/ask");
  });

  it.each([
    [200, { answer: "a", sources: [], mode: "stub" }],
    [422, { detail: [{ msg: "too short" }] }],
    [502, { detail: "LLM request failed" }],
  ])("passes through status %i and its body", async (status, body) => {
    fetchMock.mockResolvedValue(Response.json(body, { status }));

    const response = await ask();

    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
  });

  it("applies a 35 second timeout", async () => {
    const timeout = vi.spyOn(AbortSignal, "timeout");
    fetchMock.mockResolvedValue(Response.json({}));

    await ask();

    expect(timeout).toHaveBeenCalledWith(35_000);
    expect(fetchMock.mock.calls[0][1]?.signal).toBe(timeout.mock.results[0].value);
  });

  it.each([
    ["an unreachable backend", () => Promise.reject(new TypeError("fetch failed"))],
    ["a timeout", () => Promise.reject(new DOMException("timed out", "TimeoutError"))],
    ["a non-JSON reply", async () => new Response("<html>oops</html>", { status: 200 })],
    ["a non-JSON error", async () => new Response("Internal Server Error", { status: 500 })],
    ["an empty reply", async () => new Response("", { status: 200 })],
  ])("returns 502 for %s", async (_name, reply) => {
    fetchMock.mockImplementation(reply);

    const response = await ask();

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });
});
