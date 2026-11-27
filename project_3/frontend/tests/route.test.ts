// @vitest-environment node
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { POST } from "../app/api/ask/route";

const fetchMock = vi.fn<typeof fetch>();

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

function ask(body: string) {
  return POST(
    new Request("http://localhost/api/ask", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
    }),
  );
}

const question = JSON.stringify({ question: "What is alpha?" });

describe("POST /api/ask", () => {
  test.each(["", "not json", "{"])("rejects invalid JSON %j", async (body) => {
    const response = await ask(body);

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ detail: "Invalid JSON" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("forwards the body to BACKEND_URL", async () => {
    fetchMock.mockResolvedValue(Response.json({}));

    await ask(question);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://backend.test:9000/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe("application/json");
    expect(init?.body).toBe(question);
  });

  test("accepts a trailing slash on BACKEND_URL", async () => {
    vi.stubEnv("BACKEND_URL", "http://backend.test:9000/");
    fetchMock.mockResolvedValue(Response.json({}));

    await ask(question);

    expect(fetchMock.mock.calls[0][0]).toBe("http://backend.test:9000/ask");
  });

  test.each([undefined, ""])(
    "defaults to port 8000 on loopback when BACKEND_URL is %j",
    async (value) => {
      vi.stubEnv("BACKEND_URL", value);
      fetchMock.mockResolvedValue(Response.json({}));

      await ask(question);

      expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:8000/ask");
    },
  );

  test("reads BACKEND_URL on every request", async () => {
    fetchMock.mockImplementation(async () => Response.json({}));

    await ask(question);
    vi.stubEnv("BACKEND_URL", "http://other.test");
    await ask(question);

    expect(fetchMock.mock.calls[1][0]).toBe("http://other.test/ask");
  });

  test.each([
    [200, { answer: "A", sources: [], mode: "stub" }],
    [422, { detail: [{ msg: "too short" }] }],
    [502, { detail: "LLM request failed" }],
  ])("passes through status %i and its body", async (status, body) => {
    fetchMock.mockResolvedValue(Response.json(body, { status }));

    const response = await ask(question);

    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
  });

  async function expectUnavailable(response: Response) {
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  }

  test("returns 502 when the backend is unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    await expectUnavailable(await ask(question));
  });

  test.each([200, 500])(
    "returns 502 for a non-JSON reply with status %i",
    async (status) => {
      fetchMock.mockResolvedValue(new Response("<html>oops</html>", { status }));

      await expectUnavailable(await ask(question));
    },
  );

  test("gives up after 35 seconds", async () => {
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

    await expectUnavailable(await ask(question));
    expect(timeout).toHaveBeenCalledWith(35_000);
  });
});
