import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "../app/api/ask/route";

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("BACKEND_URL", "http://backend.test:9000");
});

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

function ask(body: string): Promise<Response> {
  return POST(
    new Request("http://ui.test/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    }),
  );
}

const QUESTION = JSON.stringify({ question: "Where is the Moon?" });

function reply(status: number, body: unknown): void {
  fetchMock.mockResolvedValue(Response.json(body, { status }));
}

describe("POST /api/ask", () => {
  it.each(["", "not json", '{"question":'])(
    "rejects invalid JSON %j with 400",
    async (body) => {
      const response = await ask(body);

      expect(response.status).toBe(400);
      expect(await response.json()).toEqual({ detail: "Invalid JSON" });
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("forwards the body to BACKEND_URL/ask", async () => {
    reply(200, {});

    await ask(QUESTION);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://backend.test:9000/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(init?.body).toBe(QUESTION);
  });

  it("tolerates a trailing slash on BACKEND_URL", async () => {
    vi.stubEnv("BACKEND_URL", "http://backend.test:9000/");
    reply(200, {});

    await ask(QUESTION);

    expect(fetchMock.mock.calls[0][0]).toBe("http://backend.test:9000/ask");
  });

  it("reads BACKEND_URL on every request", async () => {
    fetchMock.mockImplementation(async () => Response.json({}));

    await ask(QUESTION);
    vi.stubEnv("BACKEND_URL", "http://other.test");
    await ask(QUESTION);

    expect(fetchMock.mock.calls[1][0]).toBe("http://other.test/ask");
  });

  it.each([undefined, ""])(
    "defaults to port 8000 on 127.0.0.1 when BACKEND_URL is %j",
    async (value) => {
      vi.stubEnv("BACKEND_URL", value);
      reply(200, {});

      await ask(QUESTION);

      expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:8000/ask");
    },
  );

  it.each([
    [200, { answer: "a", sources: [], mode: "stub" }],
    [422, { detail: [{ msg: "too short" }] }],
    [502, { detail: "LLM request failed" }],
  ])("passes through status %i and its body", async (status, body) => {
    reply(status, body);

    const response = await ask(QUESTION);

    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
  });

  it("gives up after 35 seconds", async () => {
    const controller = new AbortController();
    const timeout = vi
      .spyOn(AbortSignal, "timeout")
      .mockReturnValue(controller.signal);
    fetchMock.mockImplementation(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(init.signal?.reason),
          );
          controller.abort(new DOMException("timed out", "TimeoutError"));
        }),
    );

    const response = await ask(QUESTION);

    expect(timeout).toHaveBeenCalledWith(35_000);
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });

  it("returns 502 when the backend is unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    const response = await ask(QUESTION);

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });

  it.each([200, 500])(
    "returns 502 for a non-JSON reply with status %i",
    async (status) => {
      fetchMock.mockResolvedValue(
        new Response("<h1>Internal Server Error</h1>", { status }),
      );

      const response = await ask(QUESTION);

      expect(response.status).toBe(502);
      expect(await response.json()).toEqual({ detail: "Backend unavailable" });
    },
  );
});
