import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "../app/api/ask/route";

const fetchMock = vi.fn<typeof fetch>();

function post(body: string): Promise<Response> {
  return POST(
    new Request("http://frontend.test/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    }),
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("BACKEND_URL", "http://backend.test:9999");
  fetchMock.mockImplementation(async () => Response.json({ answer: "ok" }));
});

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("POST /api/ask", () => {
  it.each(["", "{not json", "question=hi"])(
    "rejects invalid JSON %j without calling the backend",
    async (body) => {
      const response = await post(body);

      expect(response.status).toBe(400);
      expect(await response.json()).toEqual({ detail: "Invalid JSON" });
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("forwards the body to <BACKEND_URL>/ask", async () => {
    const body = JSON.stringify({ question: "What is up?" });

    await post(body);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://backend.test:9999/ask");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(body);
    expect(new Headers(init?.headers).get("content-type")).toBe("application/json");
  });

  it("accepts a trailing slash on BACKEND_URL", async () => {
    vi.stubEnv("BACKEND_URL", "http://backend.test:9999/");

    await post("{}");

    expect(fetchMock.mock.calls[0][0]).toBe("http://backend.test:9999/ask");
  });

  it("reads BACKEND_URL on every request", async () => {
    await post("{}");
    vi.stubEnv("BACKEND_URL", "http://other.test");
    await post("{}");

    expect(fetchMock.mock.calls[1][0]).toBe("http://other.test/ask");
  });

  it("defaults to port 8000 on 127.0.0.1", async () => {
    vi.stubEnv("BACKEND_URL", undefined);

    await post("{}");

    expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:8000/ask");
  });

  it.each([
    [200, { answer: "a", sources: [], mode: "stub" }],
    [422, { detail: [{ msg: "too short" }] }],
    [502, { detail: "LLM request failed" }],
  ])("passes through status %i and its JSON body", async (status, body) => {
    fetchMock.mockResolvedValue(Response.json(body, { status }));

    const response = await post("{}");

    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
  });

  it("returns 502 when the backend is unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    const response = await post("{}");

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });

  it.each([200, 500])("returns 502 for a non-JSON %i reply", async (status) => {
    fetchMock.mockResolvedValue(new Response("<html>oops</html>", { status }));

    const response = await post("{}");

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });

  it("gives up after 35 seconds with a 502", async () => {
    const controller = new AbortController();
    const timeout = vi.spyOn(AbortSignal, "timeout").mockReturnValue(controller.signal);
    fetchMock.mockImplementation(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(init.signal?.reason));
          controller.abort(new DOMException("timed out", "TimeoutError"));
        }),
    );

    const response = await post("{}");

    expect(timeout).toHaveBeenCalledWith(35_000);
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });
});
