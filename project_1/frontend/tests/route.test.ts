// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "../app/api/ask/route";

const fetchMock = vi.fn<typeof fetch>();

function ask(body = JSON.stringify({ question: "cats?" })) {
  return POST(
    new Request("http://frontend.test/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    }),
  );
}

beforeEach(() => {
  vi.stubEnv("BACKEND_URL", "http://backend.test:9999");
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  fetchMock.mockImplementation(async () => Response.json({ ok: true }));
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("POST /api/ask", () => {
  it.each(["", "not json", "{"])("rejects invalid JSON %j", async (body) => {
    const response = await ask(body);

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ detail: "Invalid JSON" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the body to BACKEND_URL", async () => {
    await ask('{"question":"cats?"}');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://backend.test:9999/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(init?.body).toBe('{"question":"cats?"}');
  });

  it("ignores a trailing slash in BACKEND_URL", async () => {
    vi.stubEnv("BACKEND_URL", "http://backend.test:9999/");

    await ask();

    expect(fetchMock.mock.calls[0][0]).toBe("http://backend.test:9999/ask");
  });

  it.each([undefined, ""])(
    "uses port 8000 on 127.0.0.1 when BACKEND_URL is %j",
    async (value) => {
      vi.stubEnv("BACKEND_URL", value);

      await ask();

      expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:8000/ask");
    },
  );

  it("reads BACKEND_URL on every request", async () => {
    await ask();
    vi.stubEnv("BACKEND_URL", "http://other.test");
    await ask();

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "http://backend.test:9999/ask",
      "http://other.test/ask",
    ]);
  });

  it.each([
    [200, { answer: "Yes.", sources: [], mode: "stub" }],
    [422, { detail: [{ msg: "too short" }] }],
    [502, { detail: "LLM request failed" }],
  ])("passes through a %i reply", async (status, body) => {
    fetchMock.mockImplementation(async () => Response.json(body, { status }));

    const response = await ask();

    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
  });

  it("gives 502 when the backend is unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    const response = await ask();

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });

  it.each([200, 500])(
    "gives 502 when a %i reply is not JSON",
    async (status) => {
      fetchMock.mockImplementation(
        async () => new Response("<h1>oops</h1>", { status }),
      );

      const response = await ask();

      expect(response.status).toBe(502);
      expect(await response.json()).toEqual({ detail: "Backend unavailable" });
    },
  );

  it("gives up after 35 seconds with 502", async () => {
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

    const response = await ask();

    expect(timeout).toHaveBeenCalledWith(35_000);
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });
});
