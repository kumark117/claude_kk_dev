// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POST } from "../app/api/ask/route";

const fetchMock = vi.fn<typeof fetch>();

function ask(body: string) {
  return POST(
    new Request("http://frontend.test/api/ask", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
    }),
  );
}

const QUESTION = JSON.stringify({ question: "How do rockets fly?" });

beforeEach(() => {
  vi.stubEnv("BACKEND_URL", "http://backend.test:9999");
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation(async () => Response.json({ answer: "ok" }));
});

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("POST /api/ask", () => {
  it.each(["", "not json", '{"question":'])(
    "rejects invalid JSON %j with 400 without calling the backend",
    async (body) => {
      const response = await ask(body);

      expect(response.status).toBe(400);
      expect(await response.json()).toEqual({ detail: "Invalid JSON" });
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("forwards the body to <BACKEND_URL>/ask", async () => {
    await ask(QUESTION);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://backend.test:9999/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(init?.body).toBe(QUESTION);
  });

  it("tolerates a trailing slash in BACKEND_URL", async () => {
    vi.stubEnv("BACKEND_URL", "http://backend.test:9999/");

    await ask(QUESTION);

    expect(fetchMock.mock.calls[0][0]).toBe("http://backend.test:9999/ask");
  });

  it.each([undefined, ""])(
    "uses port 8000 on 127.0.0.1 when BACKEND_URL is %j",
    async (value) => {
      vi.stubEnv("BACKEND_URL", value);

      await ask(QUESTION);

      expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:8000/ask");
    },
  );

  it("reads BACKEND_URL on every request", async () => {
    await ask(QUESTION);
    vi.stubEnv("BACKEND_URL", "http://other.test");
    await ask(QUESTION);

    expect(fetchMock.mock.calls[1][0]).toBe("http://other.test/ask");
  });

  it.each([
    [200, { answer: "A", sources: [], mode: "stub" }],
    [422, { detail: [{ msg: "too short" }] }],
    [502, { detail: "LLM request failed" }],
  ])("passes through status %i and its JSON body", async (status, body) => {
    fetchMock.mockResolvedValue(Response.json(body, { status }));

    const response = await ask(QUESTION);

    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
  });

  it("gives up on the backend after 35 seconds", async () => {
    const signal = new AbortController().signal;
    const timeout = vi.spyOn(AbortSignal, "timeout").mockReturnValue(signal);

    await ask(QUESTION);

    expect(timeout).toHaveBeenCalledWith(35_000);
    expect(fetchMock.mock.calls[0][1]?.signal).toBe(signal);
  });

  it.each([
    ["is unreachable", () => Promise.reject(new TypeError("fetch failed"))],
    [
      "times out",
      () => Promise.reject(new DOMException("timed out", "TimeoutError")),
    ],
    [
      "replies with non-JSON",
      async () => new Response("<html>oops</html>", { status: 200 }),
    ],
    [
      "replies with a non-JSON error",
      async () => new Response("Internal Server Error", { status: 500 }),
    ],
    ["replies with nothing", async () => new Response(null, { status: 204 })],
  ])("returns 502 when the backend %s", async (_name, reply) => {
    fetchMock.mockImplementation(reply);

    const response = await ask(QUESTION);

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });
});
