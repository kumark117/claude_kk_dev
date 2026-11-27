// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Page from "../app/page";
import AskForm from "../components/AskForm";
import type { AskResponse } from "../lib/types";

const fetchMock = vi.fn<typeof fetch>();

const ANSWER: AskResponse = {
  answer: "[stub] Based on garden.md: Water <b>daily</b>.",
  sources: [
    { file: "garden.md", text: "Water <b>daily</b>.", score: 2.3456 },
    { file: "notes.txt", text: "Prune in spring.", score: 1 },
  ],
  mode: "stub",
};

const input = () => screen.getByLabelText<HTMLInputElement>("Question");
const button = () => screen.getByRole<HTMLButtonElement>("button", { name: "Ask" });

function type(value: string) {
  fireEvent.change(input(), { target: { value } });
}

function ask(value = "How often?") {
  type(value);
  fireEvent.click(button());
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation(async () => Response.json(ANSWER));
});

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe("page", () => {
  it("has the heading and the form", () => {
    render(<Page />);

    expect(screen.getByRole("heading", { name: "Ask the docs" })).toBeTruthy();
    expect(input()).toBeTruthy();
    expect(button()).toBeTruthy();
  });
});

describe("AskForm", () => {
  it("limits the input to 1000 characters", () => {
    render(<AskForm />);

    expect(input().type).toBe("text");
    expect(input().maxLength).toBe(1000);
  });

  it("disables the button while the trimmed input is empty", () => {
    render(<AskForm />);

    expect(button().disabled).toBe(true);
    type("   ");
    expect(button().disabled).toBe(true);
    type("hi");
    expect(button().disabled).toBe(false);
  });

  it("does not send a blank question when the form is submitted", () => {
    render(<AskForm />);
    type("   ");

    fireEvent.submit(input().closest("form")!);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("submits through the form, which is what Enter does", async () => {
    render(<AskForm />);
    type("  How often?  ");

    // jsdom has no implicit submission: check the pieces Enter relies on.
    expect(button().type).toBe("submit");
    fireEvent.submit(input().closest("form")!);

    await screen.findByText(ANSWER.answer);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/ask");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ question: "How often?" });
    expect(new Headers(init?.headers).get("content-type")).toBe("application/json");
  });

  it("shows Thinking… and blocks resubmission while loading", async () => {
    let respond: (response: Response) => void = () => {};
    fetchMock.mockImplementation(
      () => new Promise<Response>((resolve) => (respond = resolve)),
    );
    render(<AskForm />);
    expect(screen.queryByText("Thinking…")).toBeNull();

    ask();

    expect(screen.getByText("Thinking…")).toBeTruthy();
    expect(button().disabled).toBe(true);
    fireEvent.submit(input().closest("form")!);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    respond(Response.json(ANSWER));

    await screen.findByText(ANSWER.answer);
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(button().disabled).toBe(false);
  });

  it("renders the answer and sources as plain text", async () => {
    const { container } = render(<AskForm />);

    ask();

    await screen.findByText(ANSWER.answer);
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    const items = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(items).toHaveLength(2);
    expect(items[0]).toContain("garden.md");
    expect(items[0]).toContain("2.35");
    expect(items[0]).toContain("Water <b>daily</b>.");
    expect(items[1]).toContain("notes.txt");
    expect(items[1]).toContain("1.00");
    expect(items[1]).toContain("Prune in spring.");
    expect(container.querySelector("b")).toBeNull();
    expect(screen.queryByText("No matching sources.")).toBeNull();
  });

  it("says when there are no matching sources", async () => {
    fetchMock.mockResolvedValue(
      Response.json({ answer: "Nothing found.", sources: [], mode: "stub" }),
    );
    render(<AskForm />);

    ask();

    await screen.findByText("Nothing found.");
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    expect(screen.getByText("No matching sources.")).toBeTruthy();
    expect(screen.queryByRole("listitem")).toBeNull();
  });

  it("shows a string error detail", async () => {
    fetchMock.mockResolvedValue(
      Response.json({ detail: "Backend unavailable" }, { status: 502 }),
    );
    render(<AskForm />);

    ask();

    expect((await screen.findByRole("alert")).textContent).toBe("Backend unavailable");
    expect(button().disabled).toBe(false);
  });

  it.each([
    ["a non-string detail", () => Response.json({ detail: [{ msg: "x" }] }, { status: 422 }), 422],
    ["no detail", () => Response.json({}, { status: 500 }), 500],
    ["a null body", () => Response.json(null, { status: 500 }), 500],
    ["a non-JSON body", () => new Response("<html>", { status: 503 }), 503],
  ])("shows the status for %s", async (_name, reply, status) => {
    fetchMock.mockImplementation(async () => reply());
    render(<AskForm />);

    ask();

    expect((await screen.findByRole("alert")).textContent).toBe(
      `Request failed (${status})`,
    );
  });

  it("reports a network failure", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    render(<AskForm />);

    ask();

    expect((await screen.findByRole("alert")).textContent).toBe("Request failed");
    expect(button().disabled).toBe(false);
  });

  it("clears the old answer on a new submission", async () => {
    render(<AskForm />);
    ask();
    await screen.findByText(ANSWER.answer);
    fetchMock.mockImplementation(() => new Promise<Response>(() => {}));

    ask("Another?");

    expect(screen.queryByText(ANSWER.answer)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Sources" })).toBeNull();
  });

  it("clears the old error on a new submission", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    render(<AskForm />);
    ask();
    await screen.findByRole("alert");

    ask("Another?");

    expect(screen.queryByRole("alert")).toBeNull();
    await screen.findByText(ANSWER.answer);
  });
});
