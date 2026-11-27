// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AskForm from "../components/AskForm";
import type { AskResponse } from "../lib/types";

const fetchMock = vi.fn<typeof fetch>();

const ANSWER: AskResponse = {
  answer: "The moon is grey.",
  sources: [
    { file: "moon.md", text: "Grey rock.", score: 2.345678 },
    { file: "sun.txt", text: "Hot plasma.", score: 1 },
  ],
  mode: "stub",
};

function replyWith(body: unknown, status = 200) {
  fetchMock.mockResolvedValueOnce(Response.json(body, { status }));
}

const input = () => screen.getByLabelText<HTMLInputElement>("Question");
const button = () => screen.getByRole<HTMLButtonElement>("button", { name: "Ask" });

function type(value: string) {
  fireEvent.change(input(), { target: { value } });
}

function submit(value = "moon") {
  type(value);
  fireEvent.click(button());
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
    expect(input().type).toBe("text");
    expect(input().maxLength).toBe(1000);
  });

  it("disables the button while the trimmed input is empty", () => {
    expect(button().disabled).toBe(true);
    type("   ");
    expect(button().disabled).toBe(true);
    type(" moon ");
    expect(button().disabled).toBe(false);
  });

  it("submits through a form, so Enter works", async () => {
    replyWith(ANSWER);
    type("moon");

    expect(button().type).toBe("submit");
    expect(input().form).toBe(button().form);
    fireEvent.submit(input().form!);

    expect(await screen.findByText("The moon is grey.")).toBeTruthy();
  });

  it("does not send an empty question", () => {
    type("   ");
    fireEvent.submit(input().form!);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts the trimmed question to /api/ask", async () => {
    replyWith(ANSWER);

    submit("  moon  ");
    await screen.findByText("The moon is grey.");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(JSON.parse(init?.body as string)).toEqual({ question: "moon" });
  });

  it("shows Thinking… and blocks resubmission while loading", async () => {
    let finish!: (response: Response) => void;
    fetchMock.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));

    submit();

    expect(screen.getByText("Thinking…")).toBeTruthy();
    expect(button().disabled).toBe(true);
    fireEvent.submit(input().form!);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    finish(Response.json(ANSWER));

    await screen.findByText("The moon is grey.");
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(button().disabled).toBe(false);
  });

  it("renders the answer and each source", async () => {
    replyWith(ANSWER);

    submit();

    expect(await screen.findByText("The moon is grey.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toHaveLength(2);
    for (const part of ["moon.md", "2.35", "Grey rock."]) {
      expect(items[0]).toContain(part);
    }
    for (const part of ["sun.txt", "1.00", "Hot plasma."]) {
      expect(items[1]).toContain(part);
    }
    expect(screen.queryByText("No matching sources.")).toBeNull();
  });

  it("says when there are no sources", async () => {
    replyWith({ answer: "Nothing found.", sources: [], mode: "stub" });

    submit();

    expect(await screen.findByText("No matching sources.")).toBeTruthy();
    expect(screen.getByText("Nothing found.")).toBeTruthy();
  });

  it("renders returned text as plain text", async () => {
    replyWith({
      answer: "<b>bold</b>",
      sources: [{ file: "<i>x</i>.md", text: "<img src=x>", score: 1 }],
      mode: "stub",
    });

    submit();

    expect(await screen.findByText("<b>bold</b>")).toBeTruthy();
    expect(screen.getByText("<img src=x>")).toBeTruthy();
    expect(document.querySelector("b, i, img")).toBeNull();
  });

  it("shows a string error detail", async () => {
    replyWith({ detail: "Backend unavailable" }, 502);

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Backend unavailable",
    );
    expect(button().disabled).toBe(false);
  });

  it.each([
    ["an array detail", Response.json({ detail: [{ msg: "x" }] }, { status: 422 }), 422],
    ["no detail", Response.json({}, { status: 500 }), 500],
    ["a null body", Response.json(null, { status: 503 }), 503],
    ["a non-JSON body", new Response("oops", { status: 504 }), 504],
    ["an unreadable success", new Response("oops", { status: 200 }), 200],
  ])("shows the status for %s", async (_name, response, status) => {
    fetchMock.mockResolvedValueOnce(response);

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      `Request failed (${status})`,
    );
  });

  it("shows a network failure", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Request failed",
    );
    expect(button().disabled).toBe(false);
  });

  it("clears the previous answer on a new submission", async () => {
    replyWith(ANSWER);
    submit();
    await screen.findByText("The moon is grey.");

    fetchMock.mockReturnValueOnce(new Promise(() => {}));
    submit("sun");

    expect(screen.queryByText("The moon is grey.")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Sources" })).toBeNull();
  });

  it("clears the previous error on a new submission", async () => {
    replyWith({ detail: "LLM request failed" }, 502);
    submit();
    await screen.findByRole("alert");

    replyWith(ANSWER);
    submit("sun");

    expect(screen.queryByRole("alert")).toBeNull();
    await waitFor(() =>
      expect(screen.getByText("The moon is grey.")).toBeTruthy(),
    );
  });
});
