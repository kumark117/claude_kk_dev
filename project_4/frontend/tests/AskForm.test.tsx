// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AskForm from "../components/AskForm";
import type { AskResponse } from "../lib/types";

const fetchMock = vi.fn<typeof fetch>();

const RESULT: AskResponse = {
  answer: "[stub] Based on tea.md: Steep green tea briefly.",
  sources: [
    { file: "tea.md", text: "Steep green tea briefly.", score: 1.23456 },
    { file: "water.txt", text: "<b>Use soft water.</b>", score: 0.5 },
  ],
  mode: "stub",
};

function input(): HTMLInputElement {
  return screen.getByLabelText("Question");
}

function button(): HTMLButtonElement {
  return screen.getByRole("button", { name: "Ask" });
}

function type(value: string): void {
  fireEvent.change(input(), { target: { value } });
}

function submit(value = "How long to steep tea?"): void {
  type(value);
  fireEvent.click(button());
}

function replies(status: number, body: unknown): void {
  fetchMock.mockResolvedValueOnce(Response.json(body, { status }));
}

/** A fetch reply that stays pending until the test resolves it. */
function pendingReply(): (response: Response) => void {
  let resolve!: (response: Response) => void;
  fetchMock.mockReturnValueOnce(
    new Promise<Response>((done) => {
      resolve = done;
    }),
  );
  return resolve;
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  render(<AskForm />);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AskForm", () => {
  it("has a text input limited to 1000 characters", () => {
    expect(input().type).toBe("text");
    expect(input().maxLength).toBe(1000);
  });

  it("disables the button while the trimmed input is empty", () => {
    expect(button().disabled).toBe(true);
    type("   ");
    expect(button().disabled).toBe(true);
    type("tea");
    expect(button().disabled).toBe(false);
  });

  it("does not submit a blank question", () => {
    type("   ");
    fireEvent.submit(input().closest("form")!);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("submits through the form, so Enter works", () => {
    pendingReply();
    type("tea");

    expect(button().type).toBe("submit");
    fireEvent.submit(input().closest("form")!);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("posts the question to /api/ask", async () => {
    replies(200, RESULT);

    submit("  tea?  ");
    await screen.findByText(RESULT.answer);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(JSON.parse(init?.body as string)).toEqual({ question: "tea?" });
  });

  it("shows Thinking and blocks resubmission while loading", async () => {
    const resolve = pendingReply();
    expect(screen.queryByText("Thinking…")).toBeNull();

    submit();

    expect(screen.getByText("Thinking…")).toBeTruthy();
    expect(button().disabled).toBe(true);
    fireEvent.submit(input().closest("form")!);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    resolve(Response.json(RESULT));
    await screen.findByText(RESULT.answer);
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(button().disabled).toBe(false);
  });

  it("renders the answer and each source as plain text", async () => {
    replies(200, RESULT);

    submit();

    expect(await screen.findByText(RESULT.answer)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("tea.md");
    expect(items[0].textContent).toContain("1.23");
    expect(items[0].textContent).not.toContain("1.234");
    expect(items[0].textContent).toContain("Steep green tea briefly.");
    expect(items[1].textContent).toContain("water.txt");
    expect(items[1].textContent).toContain("0.50");
    expect(items[1].textContent).toContain("<b>Use soft water.</b>");
    expect(items[1].querySelector("b")).toBeNull();
    expect(screen.queryByText("No matching sources.")).toBeNull();
  });

  it("says when there are no sources", async () => {
    replies(200, { answer: "Nothing found.", sources: [], mode: "stub" });

    submit();

    expect(await screen.findByText("No matching sources.")).toBeTruthy();
    expect(screen.getByText("Nothing found.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  it("shows a string error detail", async () => {
    replies(502, { detail: "Backend unavailable" });

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Backend unavailable",
    );
    expect(button().disabled).toBe(false);
  });

  it.each([
    ["a list", { detail: [{ msg: "too short" }] }],
    ["missing", {}],
    ["null", null],
  ])("shows the status when the detail is %s", async (_label, body) => {
    replies(422, body);

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Request failed (422)",
    );
  });

  it("shows the status when the error reply is not JSON", async () => {
    fetchMock.mockResolvedValueOnce(new Response("oops", { status: 500 }));

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Request failed (500)",
    );
  });

  it("reports a network failure", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Request failed",
    );
    expect(button().disabled).toBe(false);
  });

  it("clears the previous answer when submitting again", async () => {
    replies(200, RESULT);
    submit();
    await screen.findByText(RESULT.answer);
    pendingReply();

    fireEvent.click(button());

    expect(screen.queryByText(RESULT.answer)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Sources" })).toBeNull();
  });

  it("clears the previous error when submitting again", async () => {
    replies(502, { detail: "LLM request failed" });
    submit();
    await screen.findByRole("alert");
    pendingReply();

    fireEvent.click(button());

    expect(screen.queryByRole("alert")).toBeNull();
  });
});
