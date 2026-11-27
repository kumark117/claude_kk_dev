// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Page from "../app/page";
import AskForm from "../components/AskForm";
import type { AskResponse } from "../lib/types";

const fetchMock = vi.fn<typeof fetch>();

const SUCCESS: AskResponse = {
  answer: "[stub] Based on volcano.md: Lava is molten rock.",
  sources: [
    { file: "volcano.md", text: "Lava is molten rock.", score: 1.23456 },
    { file: "ocean.txt", text: "The ocean is salty.", score: 0.5 },
  ],
  mode: "stub",
};

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const input = () => screen.getByLabelText<HTMLInputElement>("Question");
const button = () => screen.getByRole<HTMLButtonElement>("button", { name: "Ask" });

function type(value: string): void {
  fireEvent.change(input(), { target: { value } });
}

function submit(value = "What is lava?"): void {
  type(value);
  fireEvent.click(button());
}

function reply(status: number, body: unknown): void {
  fetchMock.mockImplementationOnce(async () => Response.json(body, { status }));
}

/** A fetch that stays pending until the test resolves it. */
function pendingReply(): (response: Response) => void {
  let resolve!: (response: Response) => void;
  fetchMock.mockImplementationOnce(
    () =>
      new Promise<Response>((done) => {
        resolve = done;
      }),
  );
  return (response) => resolve(response);
}

describe("Page", () => {
  it("shows the heading and the form", () => {
    render(<Page />);

    expect(screen.getByRole("heading", { name: "Ask the docs" })).toBeTruthy();
    expect(input()).toBeTruthy();
    expect(button()).toBeTruthy();
  });
});

describe("AskForm", () => {
  it("has a labelled text input limited to 1000 characters", () => {
    render(<AskForm />);

    expect(input().type).toBe("text");
    expect(input().maxLength).toBe(1000);
  });

  it("disables the button while the trimmed input is empty", () => {
    render(<AskForm />);

    expect(button().disabled).toBe(true);
    type("   ");
    expect(button().disabled).toBe(true);
    type(" lava ");
    expect(button().disabled).toBe(false);
  });

  it("submits through the form, so Enter works", async () => {
    reply(200, SUCCESS);
    render(<AskForm />);
    type("What is lava?");

    expect(button().type).toBe("submit");
    fireEvent.submit(input().closest("form")!);

    expect(await screen.findByText(SUCCESS.answer)).toBeTruthy();
  });

  it("does not send a blank question", () => {
    render(<AskForm />);
    type("   ");

    fireEvent.submit(input().closest("form")!);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts the trimmed question to /api/ask", async () => {
    reply(200, SUCCESS);
    render(<AskForm />);

    submit("  What is lava?  ");
    await screen.findByText(SUCCESS.answer);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    expect(JSON.parse(String(init?.body))).toEqual({ question: "What is lava?" });
  });

  it("is disabled and shows Thinking… while a request is in flight", async () => {
    const resolve = pendingReply();
    render(<AskForm />);

    submit();

    expect(button().disabled).toBe(true);
    expect(screen.getByText("Thinking…")).toBeTruthy();
    fireEvent.submit(input().closest("form")!);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    resolve(Response.json(SUCCESS));
    await screen.findByText(SUCCESS.answer);
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(button().disabled).toBe(false);
  });

  it("renders the answer and each source with a two-decimal score", async () => {
    reply(200, SUCCESS);
    render(<AskForm />);

    submit();

    expect(await screen.findByText(SUCCESS.answer)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    const items = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(items).toHaveLength(2);
    expect(items[0]).toContain("volcano.md");
    expect(items[0]).toContain("1.23");
    expect(items[0]).not.toContain("1.234");
    expect(items[0]).toContain("Lava is molten rock.");
    expect(items[1]).toContain("ocean.txt");
    expect(items[1]).toContain("0.50");
    expect(items[1]).toContain("The ocean is salty.");
    expect(screen.queryByText("No matching sources.")).toBeNull();
  });

  it("says so when there are no sources", async () => {
    reply(200, { answer: "Nothing found.", sources: [], mode: "stub" });
    render(<AskForm />);

    submit();

    expect(await screen.findByText("No matching sources.")).toBeTruthy();
    expect(screen.getByText("Nothing found.")).toBeTruthy();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  it("renders returned text as plain text", async () => {
    const markup = '<img src="x" onerror="alert(1)">';
    reply(200, {
      answer: markup,
      sources: [{ file: "<b>f</b>.md", text: "<script>bad()</script>", score: 1 }],
      mode: "proxy",
    });
    const { container } = render(<AskForm />);

    submit();

    expect(await screen.findByText(markup)).toBeTruthy();
    expect(screen.getByText("<script>bad()</script>")).toBeTruthy();
    expect(container.querySelector("img, script, b")).toBeNull();
  });

  async function expectError(message: string): Promise<void> {
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(message);
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(button().disabled).toBe(false);
  }

  it("shows a string error detail", async () => {
    reply(502, { detail: "Backend unavailable" });
    render(<AskForm />);

    submit();

    await expectError("Backend unavailable");
  });

  it.each([
    [422, { detail: [{ msg: "too long" }] }],
    [500, {}],
    [503, null],
  ])("shows the status %i when detail is not a string", async (status, body) => {
    reply(status, body);
    render(<AskForm />);

    submit();

    await expectError(`Request failed (${status})`);
  });

  it("shows the status when the error reply is not JSON", async () => {
    fetchMock.mockResolvedValue(new Response("<html>", { status: 500 }));
    render(<AskForm />);

    submit();

    await expectError("Request failed (500)");
  });

  it("shows the status when a success reply is not JSON", async () => {
    fetchMock.mockResolvedValue(new Response("<html>", { status: 200 }));
    render(<AskForm />);

    submit();

    await expectError("Request failed (200)");
  });

  it("shows Request failed on a network failure", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    render(<AskForm />);

    submit();

    await expectError("Request failed");
  });

  it("clears the previous answer as soon as a new question is sent", async () => {
    reply(200, SUCCESS);
    render(<AskForm />);
    submit();
    await screen.findByText(SUCCESS.answer);
    const resolve = pendingReply();

    submit("Another question");

    expect(screen.queryByText(SUCCESS.answer)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Sources" })).toBeNull();
    resolve(Response.json({ detail: "LLM request failed" }, { status: 502 }));
    await expectError("LLM request failed");
  });

  it("clears the previous error as soon as a new question is sent", async () => {
    reply(502, { detail: "LLM request failed" });
    render(<AskForm />);
    submit();
    await screen.findByRole("alert");
    const resolve = pendingReply();

    submit("Another question");

    expect(screen.queryByRole("alert")).toBeNull();
    resolve(Response.json(SUCCESS));
    expect(await screen.findByText(SUCCESS.answer)).toBeTruthy();
  });
});
