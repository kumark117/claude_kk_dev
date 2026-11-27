import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POST } from "../app/api/ask/route";

const fetchMock = vi.fn<typeof fetch>();

function post(body: string): Promise<Response> {
  return POST(
    new Request("http://localhost/api/ask", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
    }),
  );
}

const question = JSON.stringify({ question: "Who makes honey?" });

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async () => Response.json({}));
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("BACKEND_URL", undefined);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("POST /api/ask", () => {
  it.each(["", "not json", "{"])("rejects invalid JSON %j", async (body) => {
    const response = await post(body);

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ detail: "Invalid JSON" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the body to port 8000 on loopback by default", async () => {
    await post(question);

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8000/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(init?.body).toBe(question);
  });

  it("reads BACKEND_URL on each request", async () => {
    vi.stubEnv("BACKEND_URL", "http://first.test:1234");
    await post(question);
    vi.stubEnv("BACKEND_URL", "http://second.test/base/");
    await post(question);

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "http://first.test:1234/ask",
      "http://second.test/base/ask",
    ]);
  });

  it.each([
    [200, { answer: "Bees.", sources: [], mode: "stub" }],
    [422, { detail: [{ msg: "too short" }] }],
    [502, { detail: "LLM request failed" }],
  ])("passes through status %i and its body", async (status, body) => {
    fetchMock.mockImplementation(async () => Response.json(body, { status }));

    const response = await post(question);

    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
  });

  async function expectUnavailable() {
    const response = await post(question);

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  }

  it("gives 502 when the backend cannot be reached", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    await expectUnavailable();
  });

  it.each([200, 500])(
    "gives 502 when a %i reply is not JSON",
    async (status) => {
      fetchMock.mockImplementation(
        async () => new Response("<html>oops</html>", { status }),
      );

      await expectUnavailable();
    },
  );

  it("gives up after 35 seconds with a 502", async () => {
    const controller = new AbortController();
    const timeout = vi
      .spyOn(AbortSignal, "timeout")
      .mockReturnValue(controller.signal);
    fetchMock.mockImplementation(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          const signal = init!.signal!;
          signal.addEventListener("abort", () => reject(signal.reason));
          controller.abort(new DOMException("timed out", "TimeoutError"));
        }),
    );

    await expectUnavailable();
    expect(timeout).toHaveBeenCalledWith(35_000);
  });
});
