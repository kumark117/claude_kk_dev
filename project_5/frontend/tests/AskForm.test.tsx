import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AskForm } from "../components/AskForm";
import type { AskResponse } from "../lib/types";

const fetchMock = vi.fn<typeof fetch>();

const RESULT: AskResponse = {
  answer: "[stub] Based on tides.txt: Tides follow the moon.",
  sources: [
    { file: "tides.txt", text: "Tides follow the moon.", score: 1.23456 },
    { file: "bees.md", text: "<b>Bees</b> make honey.", score: 0.5 },
  ],
  mode: "stub",
};

const input = () => screen.getByLabelText<HTMLInputElement>("Question");
const button = () => screen.getByRole<HTMLButtonElement>("button", { name: "Ask" });
const pageText = () => document.body.textContent ?? "";

function type(value: string) {
  fireEvent.change(input(), { target: { value } });
}

function submit(value = "Why tides?") {
  type(value);
  fireEvent.click(button());
}

function replies(status: number, body: unknown) {
  fetchMock.mockResolvedValueOnce(Response.json(body, { status }));
}

function pendingReply() {
  let resolve!: (response: Response) => void;
  fetchMock.mockReturnValueOnce(
    new Promise<Response>((done) => {
      resolve = done;
    }),
  );
  return (body: unknown) => resolve(Response.json(body));
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  render(<AskForm />);
});

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe("AskForm", () => {
  it("limits the input to 1000 characters", () => {
    expect(input().maxLength).toBe(1000);
    expect(input().type).toBe("text");
  });

  it("disables the button while the trimmed input is empty", () => {
    expect(button().disabled).toBe(true);
    type("   ");
    expect(button().disabled).toBe(true);
    type(" a ");
    expect(button().disabled).toBe(false);
  });

  it("does not submit blank input", () => {
    type("   ");

    fireEvent.submit(input().closest("form")!);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("submits through the form, so Enter works", () => {
    pendingReply();
    type("Why tides?");

    expect(button().type).toBe("submit");
    fireEvent.submit(input().closest("form")!);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("posts the trimmed question to /api/ask", async () => {
    replies(200, RESULT);

    submit("  Why tides?  ");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(JSON.parse(init?.body as string)).toEqual({
      question: "Why tides?",
    });
    await screen.findByText(RESULT.answer);
  });

  it("disables the button and shows progress while loading", async () => {
    const finish = pendingReply();

    submit();

    expect(button().disabled).toBe(true);
    expect(screen.getByText("Thinking…")).toBeTruthy();
    fireEvent.submit(input().closest("form")!);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    finish(RESULT);

    await waitFor(() => expect(button().disabled).toBe(false));
    expect(screen.queryByText("Thinking…")).toBeNull();
  });

  it("renders the answer and sources as plain text", async () => {
    replies(200, RESULT);

    submit();

    expect(await screen.findByText(RESULT.answer)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("tides.txt");
    expect(items[0].textContent).toContain("1.23");
    expect(items[0].textContent).not.toContain("1.234");
    expect(items[0].textContent).toContain("Tides follow the moon.");
    expect(items[1].textContent).toContain("0.50");
    expect(items[1].textContent).toContain("<b>Bees</b> make honey.");
    expect(items[1].querySelector("b")).toBeNull();
    expect(screen.queryByText("No matching sources.")).toBeNull();
  });

  it("says when there are no sources", async () => {
    replies(200, { answer: "Nothing found.", sources: [], mode: "stub" });

    submit();

    expect(await screen.findByText("No matching sources.")).toBeTruthy();
    expect(screen.getByText("Nothing found.")).toBeTruthy();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  it("shows a string error detail", async () => {
    replies(502, { detail: "LLM request failed" });

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "LLM request failed",
    );
    expect(button().disabled).toBe(false);
  });

  it.each([
    [422, { detail: [{ msg: "too long" }] }],
    [500, {}],
    [500, null],
  ])("shows the status %i without a string detail (%j)", async (status, body) => {
    replies(status, body);

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      `Request failed (${status})`,
    );
  });

  it("shows the status when the error body is not JSON", async () => {
    fetchMock.mockResolvedValueOnce(new Response("oops", { status: 503 }));

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Request failed (503)",
    );
  });

  it("reports a network failure", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Request failed",
    );
    expect(button().disabled).toBe(false);
  });

  it("clears the previous answer on a new submission", async () => {
    replies(200, RESULT);
    submit();
    await screen.findByText(RESULT.answer);

    pendingReply();
    submit("Another?");

    expect(pageText()).not.toContain(RESULT.answer);
    expect(screen.queryByRole("heading", { name: "Sources" })).toBeNull();
  });

  it("clears the previous error on a new submission", async () => {
    replies(502, { detail: "LLM request failed" });
    submit();
    await screen.findByRole("alert");

    pendingReply();
    submit("Another?");

    expect(screen.queryByRole("alert")).toBeNull();
  });
});
