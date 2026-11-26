// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "../app/api/ask/route";

const fetchMock = vi.fn<typeof fetch>();

function request(body: string): Request {
  return new Request("http://app.test/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });
}

const question = () => request(JSON.stringify({ question: "Why tides?" }));

function backendReplies(status: number, body: unknown) {
  fetchMock.mockResolvedValue(Response.json(body, { status }));
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
  it.each(["{not json", ""])("rejects invalid JSON %j", async (body) => {
    const response = await POST(request(body));

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ detail: "Invalid JSON" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the body to BACKEND_URL", async () => {
    backendReplies(200, {});

    await POST(question());

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://backend.test:9000/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(JSON.parse(init?.body as string)).toEqual({
      question: "Why tides?",
    });
  });

  it("reads BACKEND_URL on every request", async () => {
    fetchMock.mockImplementation(async () => Response.json({}));

    await POST(question());
    vi.stubEnv("BACKEND_URL", "http://other.test/");
    await POST(question());

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "http://backend.test:9000/ask",
      "http://other.test/ask",
    ]);
  });

  it.each([undefined, ""])(
    "uses port 8000 on loopback when BACKEND_URL is %j",
    async (value) => {
      vi.stubEnv("BACKEND_URL", value);
      backendReplies(200, {});

      await POST(question());

      expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:8000/ask");
    },
  );

  it.each([
    [200, { answer: "A", sources: [], mode: "stub" }],
    [422, { detail: [{ msg: "too short" }] }],
    [502, { detail: "LLM request failed" }],
  ])("passes through status %i and its body", async (status, body) => {
    backendReplies(status, body);

    const response = await POST(question());

    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
  });

  it("gives the backend 35 seconds", async () => {
    const controller = new AbortController();
    const timeout = vi
      .spyOn(AbortSignal, "timeout")
      .mockReturnValue(controller.signal);
    backendReplies(200, {});

    await POST(question());

    expect(timeout).toHaveBeenCalledWith(35_000);
    expect(fetchMock.mock.calls[0][1]?.signal).toBe(controller.signal);
  });

  it.each([
    ["a failed connection", new TypeError("fetch failed")],
    ["a timeout", new DOMException("timed out", "TimeoutError")],
  ])("answers 502 after %s", async (_name, error) => {
    fetchMock.mockRejectedValue(error);

    const response = await POST(question());

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });

  it.each([200, 500])(
    "answers 502 to a non-JSON reply with status %i",
    async (status) => {
      fetchMock.mockResolvedValue(
        new Response("<html>oops</html>", { status }),
      );

      const response = await POST(question());

      expect(response.status).toBe(502);
      expect(await response.json()).toEqual({ detail: "Backend unavailable" });
    },
  );
});
