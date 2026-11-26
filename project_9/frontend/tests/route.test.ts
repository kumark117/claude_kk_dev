// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "../app/api/ask/route";

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("BACKEND_URL", undefined);
});

afterEach(() => {
  fetchMock.mockReset();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

function post(body: string) {
  return POST(
    new Request("http://localhost/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    }),
  );
}

const question = JSON.stringify({ question: "penguins" });

describe("POST /api/ask", () => {
  it.each(["not json", "", "{"])(
    "rejects invalid JSON %j without calling the backend",
    async (body) => {
      const response = await post(body);

      expect(response.status).toBe(400);
      expect(await response.json()).toEqual({ detail: "Invalid JSON" });
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("forwards the body to port 8000 on 127.0.0.1 by default", async () => {
    fetchMock.mockResolvedValue(Response.json({}));

    await post(question);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8000/ask");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(question);
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
  });

  it.each(["http://127.0.0.1:9123", "http://127.0.0.1:9123/"])(
    "forwards to BACKEND_URL %s, read on each request",
    async (backend) => {
      fetchMock.mockImplementation(async () => Response.json({}));
      await post(question);
      vi.stubEnv("BACKEND_URL", backend);

      await post(question);

      expect(fetchMock.mock.calls[1][0]).toBe("http://127.0.0.1:9123/ask");
    },
  );

  it.each([
    [200, { answer: "a", sources: [], mode: "stub" }],
    [422, { detail: [{ msg: "too long" }] }],
    [502, { detail: "LLM request failed" }],
  ])("passes through status %i and its body", async (status, body) => {
    fetchMock.mockResolvedValue(Response.json(body, { status }));

    const response = await post(question);

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

    const response = await post(question);

    expect(timeout).toHaveBeenCalledWith(35_000);
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });

  it("returns 502 when the backend is unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    const response = await post(question);

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });

  it.each([200, 500])(
    "returns 502 for a non-JSON reply with status %i",
    async (status) => {
      fetchMock.mockResolvedValue(new Response("<html>oops</html>", { status }));

      const response = await post(question);

      expect(response.status).toBe(502);
      expect(await response.json()).toEqual({ detail: "Backend unavailable" });
    },
  );
});
