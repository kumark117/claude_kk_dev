// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POST } from "../app/api/ask/route";

const fetchMock = vi.fn<typeof fetch>();

function ask(body: string): Promise<Response> {
  return POST(
    new Request("http://localhost/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    }),
  );
}

const question = JSON.stringify({ question: "What boils water?" });

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("BACKEND_URL", "http://127.0.0.1:4321");
  fetchMock.mockResolvedValue(Response.json({ ok: true }));
});

afterEach(() => {
  fetchMock.mockReset();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("POST /api/ask", () => {
  it.each(["not json", "", "{"])("rejects invalid JSON %j", async (body) => {
    const response = await ask(body);

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ detail: "Invalid JSON" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the body to <BACKEND_URL>/ask", async () => {
    await ask(question);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:4321/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(init?.body).toBe(question);
  });

  it("reads BACKEND_URL on every request", async () => {
    await ask(question);
    vi.stubEnv("BACKEND_URL", "http://127.0.0.1:9999/");
    fetchMock.mockResolvedValue(Response.json({ ok: true }));
    await ask(question);

    expect(fetchMock.mock.calls[1][0]).toBe("http://127.0.0.1:9999/ask");
  });

  it.each([undefined, ""])(
    "defaults to port 8000 on 127.0.0.1 when BACKEND_URL is %j",
    async (value) => {
      vi.stubEnv("BACKEND_URL", value);

      await ask(question);

      expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:8000/ask");
    },
  );

  it.each([
    [200, { answer: "a", sources: [], mode: "stub" }],
    [422, { detail: [{ msg: "String should have at least 1 character" }] }],
    [502, { detail: "LLM request failed" }],
  ])("passes through status %i and its body", async (status, body) => {
    fetchMock.mockResolvedValue(Response.json(body, { status }));

    const response = await ask(question);

    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
  });

  it("gives up on the backend after 35 seconds", async () => {
    const timeout = vi.spyOn(AbortSignal, "timeout");

    await ask(question);

    expect(timeout).toHaveBeenCalledWith(35_000);
    expect(fetchMock.mock.calls[0][1]?.signal).toBe(
      timeout.mock.results[0].value,
    );
  });

  it.each([
    ["is unreachable", new TypeError("fetch failed")],
    ["times out", new DOMException("timed out", "TimeoutError")],
  ])("returns 502 when the backend %s", async (_name, error) => {
    fetchMock.mockRejectedValue(error);

    const response = await ask(question);

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });

  it.each([200, 500])(
    "returns 502 for a non-JSON reply with status %i",
    async (status) => {
      fetchMock.mockResolvedValue(new Response("<html>oops</html>", { status }));

      const response = await ask(question);

      expect(response.status).toBe(502);
      expect(await response.json()).toEqual({ detail: "Backend unavailable" });
    },
  );
});
