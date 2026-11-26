// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Page from "../app/page";
import AskForm from "../components/AskForm";
import type { AskResponse } from "../lib/types";

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const RESULT: AskResponse = {
  answer: "[stub] Based on space.md: <b>Rockets</b>",
  sources: [
    { file: "space.md", text: "<b>Rockets</b> reach orbit.", score: 2.345678 },
    { file: "tea.txt", text: "Tea is steeped.", score: 1 },
  ],
  mode: "stub",
};

const input = () => screen.getByLabelText<HTMLInputElement>("Question");
const button = () => screen.getByRole<HTMLButtonElement>("button", { name: "Ask" });

function type(value: string) {
  fireEvent.change(input(), { target: { value } });
}

function submit(value = "rockets") {
  type(value);
  fireEvent.click(button());
}

describe("Page", () => {
  it("shows the heading and the form", () => {
    render(<Page />);

    expect(screen.getByRole("heading", { name: "Ask the docs" })).toBeTruthy();
    expect(input()).toBeTruthy();
  });
});

describe("AskForm", () => {
  it("has a labelled text input limited to 1000 characters", () => {
    render(<AskForm />);

    expect(input().type).toBe("text");
    expect(input().maxLength).toBe(1000);
    expect(button().type).toBe("submit");
  });

  it("disables the button while the trimmed input is empty", () => {
    render(<AskForm />);
    expect(button().disabled).toBe(true);

    type("   ");
    expect(button().disabled).toBe(true);

    type(" a ");
    expect(button().disabled).toBe(false);
  });

  it("does not submit blank input", () => {
    render(<AskForm />);
    type("   ");

    fireEvent.submit(input().form!);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts the question to /api/ask when the form is submitted", async () => {
    fetchMock.mockResolvedValue(Response.json(RESULT));
    render(<AskForm />);
    type("  rockets  ");

    // Pressing Enter in a form input submits the form.
    fireEvent.submit(input().form!);

    await screen.findByText("Sources");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(JSON.parse(init?.body as string)).toEqual({ question: "rockets" });
  });

  it("disables the button and shows Thinking… while a request is in flight", async () => {
    let respond!: (response: Response) => void;
    fetchMock.mockReturnValue(
      new Promise((resolve) => {
        respond = resolve;
      }),
    );
    render(<AskForm />);
    expect(screen.queryByText("Thinking…")).toBeNull();

    submit();

    expect(screen.getByText("Thinking…")).toBeTruthy();
    expect(button().disabled).toBe(true);
    fireEvent.submit(input().form!);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    respond(Response.json(RESULT));

    await screen.findByText("Sources");
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(button().disabled).toBe(false);
  });

  it("renders the answer and each source as plain text", async () => {
    fetchMock.mockResolvedValue(Response.json(RESULT));
    const { container } = render(<AskForm />);

    submit();

    expect(await screen.findByText(RESULT.answer)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    const items = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(items).toHaveLength(2);
    expect(items[0]).toContain("space.md");
    expect(items[0]).toContain("2.35");
    expect(items[0]).toContain("<b>Rockets</b> reach orbit.");
    expect(items[1]).toContain("tea.txt");
    expect(items[1]).toContain("1.00");
    expect(items[1]).toContain("Tea is steeped.");
    expect(container.querySelector("b")).toBeNull();
    expect(screen.queryByText("No matching sources.")).toBeNull();
  });

  it("says when there are no matching sources", async () => {
    fetchMock.mockResolvedValue(
      Response.json({ answer: "Nothing found.", sources: [], mode: "stub" }),
    );
    render(<AskForm />);

    submit();

    expect(await screen.findByText("No matching sources.")).toBeTruthy();
    expect(screen.getByText("Nothing found.")).toBeTruthy();
    expect(screen.queryByRole("listitem")).toBeNull();
  });

  it("shows a string error detail", async () => {
    fetchMock.mockResolvedValue(
      Response.json({ detail: "Backend unavailable" }, { status: 502 }),
    );
    render(<AskForm />);

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Backend unavailable",
    );
    expect(button().disabled).toBe(false);
  });

  it.each([
    ["a list detail", Response.json({ detail: [{ msg: "x" }] }, { status: 422 })],
    ["no detail", Response.json({}, { status: 422 })],
    ["a null body", Response.json(null, { status: 422 })],
    ["a non-JSON body", new Response("oops", { status: 422 })],
  ])("shows the status for an error with %s", async (_name, response) => {
    fetchMock.mockResolvedValue(response);
    render(<AskForm />);

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Request failed (422)",
    );
  });

  it("shows Request failed on a network failure", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    render(<AskForm />);

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe("Request failed");
    expect(button().disabled).toBe(false);
  });

  it("clears the previous answer and error on a new submission", async () => {
    fetchMock
      .mockResolvedValueOnce(Response.json(RESULT))
      .mockResolvedValueOnce(
        Response.json({ detail: "LLM request failed" }, { status: 502 }),
      )
      .mockReturnValueOnce(new Promise(() => {}));
    render(<AskForm />);

    submit("first");
    await screen.findByText(RESULT.answer);

    submit("second");
    expect(screen.queryByText(RESULT.answer)).toBeNull();
    expect(screen.queryByText("Sources")).toBeNull();
    await screen.findByRole("alert");

    submit("third");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("Thinking…")).toBeTruthy();
  });
});
