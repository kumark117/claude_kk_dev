// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "../app/api/ask/route";

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("BACKEND_URL", "http://127.0.0.1:9999");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

function ask(body: string): Promise<Response> {
  return POST(
    new Request("http://localhost/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    }),
  );
}

function reply(status: number, body: unknown): void {
  fetchMock.mockResolvedValue(Response.json(body, { status }));
}

describe("POST /api/ask", () => {
  it.each(["", "{not json", "question=lava"])(
    "rejects invalid JSON %j without calling the backend",
    async (body) => {
      const response = await ask(body);

      expect(response.status).toBe(400);
      expect(await response.json()).toEqual({ detail: "Invalid JSON" });
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("forwards the body to BACKEND_URL/ask", async () => {
    reply(200, {});

    await ask('{"question":"What is lava?"}');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:9999/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    expect(JSON.parse(String(init?.body))).toEqual({ question: "What is lava?" });
  });

  it("tolerates a trailing slash on BACKEND_URL", async () => {
    vi.stubEnv("BACKEND_URL", "http://127.0.0.1:9999/");
    reply(200, {});

    await ask("{}");

    expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:9999/ask");
  });

  it("reads BACKEND_URL on every request", async () => {
    fetchMock.mockImplementation(async () => Response.json({}));

    await ask("{}");
    vi.stubEnv("BACKEND_URL", "http://127.0.0.1:7777");
    await ask("{}");

    expect(fetchMock.mock.calls[1][0]).toBe("http://127.0.0.1:7777/ask");
  });

  it.each([undefined, ""])(
    "defaults to port 8000 on 127.0.0.1 when BACKEND_URL is %j",
    async (value) => {
      vi.stubEnv("BACKEND_URL", value);
      reply(200, {});

      await ask("{}");

      expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:8000/ask");
    },
  );

  it.each([
    [200, { answer: "Hot.", sources: [], mode: "stub" }],
    [422, { detail: [{ msg: "String should have at least 1 character" }] }],
    [502, { detail: "LLM request failed" }],
  ])("passes through status %i and its JSON body", async (status, body) => {
    reply(status, body);

    const response = await ask('{"question":"q"}');

    expect(response.status).toBe(status);
    expect(response.headers.get("Content-Type")).toContain("application/json");
    expect(await response.json()).toEqual(body);
  });

  async function expectUnavailable(): Promise<void> {
    const response = await ask('{"question":"q"}');

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  }

  it("gives 502 when the backend is unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    await expectUnavailable();
  });

  it.each([
    [200, "<html>oops</html>"],
    [500, "Internal Server Error"],
    [200, ""],
  ])("gives 502 for a non-JSON reply (%i, %j)", async (status, text) => {
    fetchMock.mockResolvedValue(new Response(text, { status }));

    await expectUnavailable();
  });

  it("gives up after 35 seconds with a 502", async () => {
    const controller = new AbortController();
    const timeout = vi
      .spyOn(AbortSignal, "timeout")
      .mockReturnValue(controller.signal);
    fetchMock.mockImplementation(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(init.signal?.reason));
          controller.abort(new DOMException("timed out", "TimeoutError"));
        }),
    );

    await expectUnavailable();
    expect(timeout).toHaveBeenCalledWith(35_000);
  });
});
