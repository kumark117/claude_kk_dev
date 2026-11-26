// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "../app/api/ask/route";

const fetchMock = vi.fn<typeof fetch>();

function ask(body: string = JSON.stringify({ question: "hi" })) {
  return POST(
    new Request("http://app.test/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    }),
  );
}

function backendReplies(body: unknown, status = 200) {
  fetchMock.mockImplementation(async () => Response.json(body, { status }));
}

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

describe("POST /api/ask", () => {
  it.each(["not json", "", '{"question":'])(
    "answers 400 for invalid JSON %j without calling the backend",
    async (body) => {
      const response = await ask(body);

      expect(response.status).toBe(400);
      expect(await response.json()).toEqual({ detail: "Invalid JSON" });
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("forwards the body to <BACKEND_URL>/ask", async () => {
    backendReplies({ answer: "a", sources: [], mode: "stub" });

    await ask('{"question":"What is lava?","extra":1}');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://backend.test:9000/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(JSON.parse(init?.body as string)).toEqual({
      question: "What is lava?",
      extra: 1,
    });
  });

  it("accepts a trailing slash on BACKEND_URL", async () => {
    vi.stubEnv("BACKEND_URL", "http://backend.test:9000/");
    backendReplies({});

    await ask();

    expect(fetchMock.mock.calls[0][0]).toBe("http://backend.test:9000/ask");
  });

  it("reads BACKEND_URL on every request", async () => {
    backendReplies({});

    await ask();
    vi.stubEnv("BACKEND_URL", "http://other.test");
    await ask();

    expect(fetchMock.mock.calls[1][0]).toBe("http://other.test/ask");
  });

  it.each([undefined, ""])(
    "uses 127.0.0.1:8000 when BACKEND_URL is %j",
    async (value) => {
      vi.stubEnv("BACKEND_URL", value);
      backendReplies({});

      await ask();

      expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:8000/ask");
    },
  );

  it.each([
    [200, { answer: "a", sources: [], mode: "stub" }],
    [422, { detail: [{ msg: "too short" }] }],
    [502, { detail: "LLM request failed" }],
  ])("passes through status %i and its body", async (status, body) => {
    backendReplies(body, status);

    const response = await ask();

    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
  });

  it("gives up after 35 seconds", async () => {
    const timeout = vi.spyOn(AbortSignal, "timeout");
    backendReplies({});

    await ask();

    expect(timeout).toHaveBeenCalledWith(35_000);
    expect(fetchMock.mock.calls[0][1]?.signal).toBe(
      timeout.mock.results[0].value,
    );
  });

  it.each([
    ["unreachable", new TypeError("fetch failed")],
    ["too slow", new DOMException("timed out", "TimeoutError")],
  ])("answers 502 when the backend is %s", async (_name, error) => {
    fetchMock.mockRejectedValue(error);

    const response = await ask();

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });

  it.each([200, 500])(
    "answers 502 for a non-JSON backend reply with status %i",
    async (status) => {
      fetchMock.mockResolvedValue(new Response("<html>oops</html>", { status }));

      const response = await ask();

      expect(response.status).toBe(502);
      expect(await response.json()).toEqual({ detail: "Backend unavailable" });
    },
  );
});
