import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "../app/api/ask/route";

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("BACKEND_URL", "http://127.0.0.1:9999");
});

afterEach(() => {
  fetchMock.mockReset();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

function ask(body: string = '{"question":"tea"}') {
  return POST(
    new Request("http://localhost/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    }),
  );
}

describe("POST /api/ask", () => {
  it.each(["", "not json", '{"question":'])("rejects invalid JSON %j with 400", async (body) => {
    const response = await ask(body);

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ detail: "Invalid JSON" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the body to BACKEND_URL/ask", async () => {
    fetchMock.mockResolvedValue(Response.json({}));

    await ask('{"question":"tea","extra":1}');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:9999/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe("application/json");
    expect(init?.body).toBe('{"question":"tea","extra":1}');
  });

  it("tolerates a trailing slash on BACKEND_URL", async () => {
    vi.stubEnv("BACKEND_URL", "http://127.0.0.1:9999/");
    fetchMock.mockResolvedValue(Response.json({}));

    await ask();

    expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:9999/ask");
  });

  it("defaults to port 8000 on 127.0.0.1, reading BACKEND_URL per request", async () => {
    fetchMock.mockImplementation(async () => Response.json({}));

    vi.stubEnv("BACKEND_URL", undefined);
    await ask();
    vi.stubEnv("BACKEND_URL", "http://127.0.0.1:1234");
    await ask();

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "http://127.0.0.1:8000/ask",
      "http://127.0.0.1:1234/ask",
    ]);
  });

  it.each([
    [200, { answer: "a", sources: [], mode: "stub" }],
    [422, { detail: [{ msg: "too short" }] }],
    [502, { detail: "LLM request failed" }],
  ])("passes through status %i and its JSON body", async (status, body) => {
    fetchMock.mockResolvedValue(Response.json(body, { status }));

    const response = await ask();

    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
  });

  async function expectUnavailable(response: Response) {
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  }

  it("returns 502 when the backend is unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    await expectUnavailable(await ask());
  });

  it.each([200, 500])("returns 502 for a non-JSON %i reply", async (status) => {
    fetchMock.mockResolvedValue(new Response("<html>oops</html>", { status }));

    await expectUnavailable(await ask());
  });

  it("gives up after 35 seconds with 502", async () => {
    const controller = new AbortController();
    const timeout = vi.spyOn(AbortSignal, "timeout").mockReturnValue(controller.signal);
    fetchMock.mockImplementation(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(init.signal?.reason));
          controller.abort(new DOMException("timed out", "TimeoutError"));
        }),
    );

    const response = await ask();

    expect(timeout).toHaveBeenCalledWith(35_000);
    await expectUnavailable(response);
  });
});
