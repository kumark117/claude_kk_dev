import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { POST } from "../app/api/ask/route";

const fetchMock = vi.fn<typeof fetch>();

function ask(body: string): Promise<Response> {
  return POST(
    new Request("http://web.test/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    }),
  );
}

const question = JSON.stringify({ question: "What is lava?" });

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("BACKEND_URL", "http://backend.test:9999");
  fetchMock.mockImplementation(async () => Response.json({ ok: true }));
});

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("POST /api/ask", () => {
  test.each(["", "{not json", "question=hi"])(
    "rejects invalid JSON %j with 400 without calling the backend",
    async (body) => {
      const response = await ask(body);

      expect(response.status).toBe(400);
      expect(await response.json()).toEqual({ detail: "Invalid JSON" });
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  test("forwards the body to <BACKEND_URL>/ask", async () => {
    await ask(question);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://backend.test:9999/ask");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(question);
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
  });

  test("tolerates a trailing slash on BACKEND_URL", async () => {
    vi.stubEnv("BACKEND_URL", "http://backend.test:9999/");

    await ask(question);

    expect(fetchMock.mock.calls[0][0]).toBe("http://backend.test:9999/ask");
  });

  test("reads BACKEND_URL on every request", async () => {
    await ask(question);
    vi.stubEnv("BACKEND_URL", "http://other.test");
    await ask(question);

    expect(fetchMock.mock.calls[1][0]).toBe("http://other.test/ask");
  });

  test.each([undefined, ""])(
    "defaults to port 8000 on 127.0.0.1 when BACKEND_URL is %j",
    async (value) => {
      vi.stubEnv("BACKEND_URL", value);

      await ask(question);

      expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:8000/ask");
    },
  );

  test.each([
    [200, { answer: "A", sources: [], mode: "stub" }],
    [422, { detail: [{ msg: "too short" }] }],
    [502, { detail: "LLM request failed" }],
  ])("passes through a %i reply", async (status, body) => {
    fetchMock.mockImplementation(async () => Response.json(body, { status }));

    const response = await ask(question);

    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
  });

  test("gives 502 when the backend is unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    const response = await ask(question);

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });

  test.each([200, 500])(
    "gives 502 when a %i backend reply is not JSON",
    async (status) => {
      fetchMock.mockImplementation(
        async () => new Response("<html>oops</html>", { status }),
      );

      const response = await ask(question);

      expect(response.status).toBe(502);
      expect(await response.json()).toEqual({ detail: "Backend unavailable" });
    },
  );

  test("gives 502 when the backend takes longer than 35 seconds", async () => {
    const controller = new AbortController();
    const timeout = vi
      .spyOn(AbortSignal, "timeout")
      .mockReturnValue(controller.signal);
    fetchMock.mockImplementation(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("timed out", "TimeoutError")),
          );
          controller.abort();
        }),
    );

    const response = await ask(question);

    expect(timeout).toHaveBeenCalledWith(35_000);
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });
});
