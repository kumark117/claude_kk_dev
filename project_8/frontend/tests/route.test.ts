// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "../app/api/ask/route";

const fetchMock = vi.fn<typeof fetch>();

function ask(body: string) {
  return POST(
    new Request("http://frontend.test/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    }),
  );
}

function backendReplies(status: number, body: unknown) {
  fetchMock.mockResolvedValue(Response.json(body, { status }));
}

async function expectUnavailable(response: Response) {
  expect(response.status).toBe(502);
  expect(await response.json()).toEqual({ detail: "Backend unavailable" });
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("BACKEND_URL", "http://backend.test:9999");
});

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("POST /api/ask", () => {
  it.each(["", "not json", '{"question":'])(
    "rejects invalid JSON %j without calling the backend",
    async (body) => {
      const response = await ask(body);

      expect(response.status).toBe(400);
      expect(await response.json()).toEqual({ detail: "Invalid JSON" });
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("forwards the body to BACKEND_URL", async () => {
    backendReplies(200, {});

    await ask('{"question":"moon"}');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://backend.test:9999/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(init?.body).toBe('{"question":"moon"}');
  });

  it("reads BACKEND_URL on each request", async () => {
    fetchMock.mockImplementation(async () => Response.json({}));

    await ask("{}");
    vi.stubEnv("BACKEND_URL", "http://other.test");
    await ask("{}");

    expect(fetchMock.mock.calls[1][0]).toBe("http://other.test/ask");
  });

  it("accepts a trailing slash on BACKEND_URL", async () => {
    vi.stubEnv("BACKEND_URL", "http://backend.test/");
    backendReplies(200, {});

    await ask("{}");

    expect(fetchMock.mock.calls[0][0]).toBe("http://backend.test/ask");
  });

  it.each([undefined, ""])(
    "defaults to port 8000 on 127.0.0.1 when BACKEND_URL is %j",
    async (value) => {
      vi.stubEnv("BACKEND_URL", value);
      backendReplies(200, {});

      await ask("{}");

      expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:8000/ask");
    },
  );

  it.each([
    [200, { answer: "a", sources: [], mode: "stub" }],
    [422, { detail: [{ msg: "too short" }] }],
    [502, { detail: "LLM request failed" }],
  ])("passes through status %i and its body", async (status, body) => {
    backendReplies(status, body);

    const response = await ask('{"question":"moon"}');

    expect(response.status).toBe(status);
    expect(response.headers.get("content-type")).toContain("application/json");
    expect(await response.json()).toEqual(body);
  });

  it("gives 502 when the backend is unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    await expectUnavailable(await ask("{}"));
  });

  it("gives up after 35 seconds with 502", async () => {
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
        }),
    );

    const pending = ask("{}");
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    controller.abort(new DOMException("timed out", "TimeoutError"));

    await expectUnavailable(await pending);
    expect(timeout).toHaveBeenCalledWith(35_000);
  });

  it.each([200, 500])(
    "gives 502 for a non-JSON reply with status %i",
    async (status) => {
      fetchMock.mockResolvedValue(new Response("<h1>oops</h1>", { status }));

      await expectUnavailable(await ask("{}"));
    },
  );
});
