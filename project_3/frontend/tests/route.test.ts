import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "../app/api/ask/route";

const fetchMock = vi.fn<typeof fetch>();

function ask(body: string): Promise<Response> {
  return POST(new Request("http://test.local/api/ask", { method: "POST", body }));
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
  it.each(["", "not json", "{"])("rejects invalid JSON %j with 400", async (body) => {
    const response = await ask(body);

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ detail: "Invalid JSON" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the body to BACKEND_URL/ask", async () => {
    fetchMock.mockResolvedValue(Response.json({}));

    await ask('{"question":"hi"}');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:9999/ask");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe('{"question":"hi"}');
    expect(new Headers(init?.headers).get("content-type")).toBe("application/json");
  });

  it("reads BACKEND_URL on each request and tolerates a trailing slash", async () => {
    fetchMock.mockImplementation(async () => Response.json({}));

    await ask("{}");
    vi.stubEnv("BACKEND_URL", "http://127.0.0.1:8888/");
    await ask("{}");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "http://127.0.0.1:9999/ask",
      "http://127.0.0.1:8888/ask",
    ]);
  });

  it.each([undefined, ""])("defaults to port 8000 when BACKEND_URL is %j", async (value) => {
    vi.stubEnv("BACKEND_URL", value);
    fetchMock.mockResolvedValue(Response.json({}));

    await ask("{}");

    expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:8000/ask");
  });

  it("uses a 35 second timeout", async () => {
    const timeout = vi.spyOn(AbortSignal, "timeout");
    fetchMock.mockResolvedValue(Response.json({}));

    await ask("{}");

    expect(timeout).toHaveBeenCalledWith(35_000);
    expect(fetchMock.mock.calls[0][1]?.signal).toBe(timeout.mock.results[0].value);
  });

  it.each([
    [200, { answer: "a", sources: [], mode: "stub" }],
    [422, { detail: [{ msg: "too short" }] }],
    [502, { detail: "LLM request failed" }],
  ])("passes through status %i and its body", async (status, body) => {
    fetchMock.mockResolvedValue(Response.json(body, { status }));

    const response = await ask('{"question":"hi"}');

    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
  });

  it.each([
    ["a failed connection", new TypeError("fetch failed")],
    ["a timeout", new DOMException("timed out", "TimeoutError")],
  ])("returns 502 for %s", async (_name, error) => {
    fetchMock.mockRejectedValue(error);

    const response = await ask("{}");

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });

  it.each([200, 500])("returns 502 for a non-JSON reply with status %i", async (status) => {
    fetchMock.mockResolvedValue(new Response("<html>oops</html>", { status }));

    const response = await ask("{}");

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });
});
