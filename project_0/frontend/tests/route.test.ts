import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POST } from "../app/api/ask/route";

const fetchMock = vi.fn<typeof fetch>();

function ask(body = JSON.stringify({ question: "lava" })) {
  return POST(
    new Request("http://localhost/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    }),
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("BACKEND_URL", undefined);
  fetchMock.mockImplementation(async () => Response.json({ ok: true }));
});

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
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
    vi.stubEnv("BACKEND_URL", "http://127.0.0.1:4321");
    const body = JSON.stringify({ question: "What is lava?" });

    await ask(body);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:4321/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(init?.body).toBe(body);
  });

  it("accepts a trailing slash on BACKEND_URL", async () => {
    vi.stubEnv("BACKEND_URL", "http://127.0.0.1:4321/");

    await ask();

    expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:4321/ask");
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
    vi.stubEnv("BACKEND_URL", "http://127.0.0.1:1111");
    await ask();
    vi.stubEnv("BACKEND_URL", "http://127.0.0.1:2222");
    await ask();

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "http://127.0.0.1:1111/ask",
      "http://127.0.0.1:2222/ask",
    ]);
  });

  it("applies a 35 second timeout", async () => {
    const timeout = vi.spyOn(AbortSignal, "timeout");

    await ask();

    expect(timeout).toHaveBeenCalledWith(35_000);
    expect(fetchMock.mock.calls[0][1]?.signal).toBe(
      timeout.mock.results[0].value,
    );
  });

  it.each([
    [200, { answer: "a", sources: [], mode: "stub" }],
    [422, { detail: [{ msg: "too short" }] }],
    [502, { detail: "LLM request failed" }],
  ])("passes through status %i and its body", async (status, body) => {
    fetchMock.mockImplementation(async () => Response.json(body, { status }));

    const response = await ask();

    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
  });

  async function expectUnavailable() {
    const response = await ask();

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  }

  it("returns 502 when the backend is unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    await expectUnavailable();
  });

  it("returns 502 when the backend times out", async () => {
    fetchMock.mockRejectedValue(new DOMException("slow", "TimeoutError"));

    await expectUnavailable();
  });

  it.each([200, 500])(
    "returns 502 for a non-JSON reply with status %i",
    async (status) => {
      fetchMock.mockImplementation(
        async () => new Response("<html>oops</html>", { status }),
      );

      await expectUnavailable();
    },
  );
});
