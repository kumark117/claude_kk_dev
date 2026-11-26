// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "../app/api/ask/route";

const fetchMock = vi.fn<typeof fetch>();

function ask(body: string): Promise<Response> {
  return POST(
    new Request("http://app.test/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    }),
  );
}

function backendReplies(status: number, body: unknown): void {
  fetchMock.mockResolvedValue(Response.json(body, { status }));
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("BACKEND_URL", "http://backend.test:9000");
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("POST /api/ask", () => {
  it.each(["", "{", "not json"])(
    "answers 400 for invalid JSON %j without calling the backend",
    async (body) => {
      const response = await ask(body);

      expect(response.status).toBe(400);
      expect(await response.json()).toEqual({ detail: "Invalid JSON" });
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("forwards the body to BACKEND_URL", async () => {
    backendReplies(200, {});

    await ask('{"question":"hello"}');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://backend.test:9000/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(init?.body).toBe('{"question":"hello"}');
  });

  it("ignores a trailing slash on BACKEND_URL", async () => {
    vi.stubEnv("BACKEND_URL", "http://backend.test:9000/");
    backendReplies(200, {});

    await ask("{}");

    expect(fetchMock.mock.calls[0][0]).toBe("http://backend.test:9000/ask");
  });

  it.each([undefined, ""])(
    "uses port 8000 on 127.0.0.1 when BACKEND_URL is %j",
    async (value) => {
      vi.stubEnv("BACKEND_URL", value);
      backendReplies(200, {});

      await ask("{}");

      expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:8000/ask");
    },
  );

  it("reads BACKEND_URL on every request", async () => {
    fetchMock.mockImplementation(async () => Response.json({}));

    await ask("{}");
    vi.stubEnv("BACKEND_URL", "http://other.test");
    await ask("{}");

    expect(fetchMock.mock.calls[1][0]).toBe("http://other.test/ask");
  });

  it.each([
    [200, { answer: "yes", sources: [], mode: "stub" }],
    [422, { detail: [{ msg: "too short" }] }],
    [502, { detail: "LLM request failed" }],
  ])("passes through status %i and its JSON body", async (status, body) => {
    backendReplies(status, body);

    const response = await ask('{"question":"q"}');

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
            reject(new DOMException("timed out", "TimeoutError")),
          );
        }),
    );

    const pending = ask("{}");
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    controller.abort();
    const response = await pending;

    expect(timeout).toHaveBeenCalledWith(35_000);
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });

  it("answers 502 when the backend cannot be reached", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    const response = await ask("{}");

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });

  it.each([200, 500])(
    "answers 502 when the backend sends non-JSON with status %i",
    async (status) => {
      fetchMock.mockResolvedValue(new Response("<html>oops</html>", { status }));

      const response = await ask("{}");

      expect(response.status).toBe(502);
      expect(await response.json()).toEqual({ detail: "Backend unavailable" });
    },
  );
});
