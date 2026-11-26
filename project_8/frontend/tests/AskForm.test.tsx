// @vitest-environment jsdom
import { cleanup, configure, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AskForm from "../components/AskForm";
import type { AskResponse } from "../lib/types";

const fetchMock = vi.fn<typeof fetch>();

// Generous so that a busy machine does not cause spurious failures.
configure({ asyncUtilTimeout: 10_000 });

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

const ANSWER: AskResponse = {
  answer: "Boil the water first.",
  sources: [
    { file: "tea.md", text: "Boil the kettle.", score: 1.23456 },
    { file: "coffee.txt", text: "Grind the beans.", score: 0.5 },
  ],
  mode: "stub",
};

const input = () => screen.getByLabelText("Question") as HTMLInputElement;
const button = () => screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement;
const type = (value: string) => fireEvent.change(input(), { target: { value } });

function submit(value = "How do I make tea?") {
  type(value);
  fireEvent.click(button());
}

describe("AskForm", () => {
  it("has a text input limited to 1000 characters inside a form, so Enter submits", () => {
    render(<AskForm />);

    expect(input().type).toBe("text");
    expect(input().maxLength).toBe(1000);
    expect(button().type).toBe("submit");
    expect(input().form).toBe(button().form);
    expect(input().form).not.toBeNull();
  });

  it("submits the form to /api/ask", async () => {
    fetchMock.mockResolvedValue(Response.json(ANSWER));
    render(<AskForm />);

    type("  tea  ");
    fireEvent.submit(input().form!);

    await screen.findByText(ANSWER.answer);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/ask");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ question: "tea" });
  });

  it("disables the button while the trimmed input is empty", () => {
    render(<AskForm />);

    expect(button().disabled).toBe(true);
    type("   ");
    expect(button().disabled).toBe(true);
    fireEvent.submit(input().form!);
    expect(fetchMock).not.toHaveBeenCalled();
    type("tea");
    expect(button().disabled).toBe(false);
  });

  it("disables the button and shows Thinking… while a request is in flight", async () => {
    let respond!: (response: Response) => void;
    fetchMock.mockReturnValue(new Promise((resolve) => (respond = resolve)));
    render(<AskForm />);
    expect(screen.queryByText("Thinking…")).toBeNull();

    submit();

    expect(screen.getByText("Thinking…")).toBeTruthy();
    expect(button().disabled).toBe(true);
    fireEvent.submit(input().form!);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    respond(Response.json(ANSWER));
    await screen.findByText(ANSWER.answer);
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(button().disabled).toBe(false);
  });

  it("renders the answer and each source with a two-decimal score", async () => {
    fetchMock.mockResolvedValue(Response.json(ANSWER));
    render(<AskForm />);

    submit();

    expect(await screen.findByText(ANSWER.answer)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    const items = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(items).toHaveLength(2);
    expect(items[0]).toContain("tea.md");
    expect(items[0]).toContain("1.23");
    expect(items[0]).not.toContain("1.234");
    expect(items[0]).toContain("Boil the kettle.");
    expect(items[1]).toContain("coffee.txt");
    expect(items[1]).toContain("0.50");
    expect(items[1]).toContain("Grind the beans.");
    expect(screen.queryByText("No matching sources.")).toBeNull();
  });

  it("says when there are no matching sources", async () => {
    fetchMock.mockResolvedValue(Response.json({ answer: "Nothing.", sources: [], mode: "stub" }));
    render(<AskForm />);

    submit();

    expect(await screen.findByText("No matching sources.")).toBeTruthy();
    expect(screen.getByText("Nothing.")).toBeTruthy();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  it("renders returned text as plain text", async () => {
    const markup = "<img src=x> **bold**";
    fetchMock.mockResolvedValue(
      Response.json({
        answer: markup,
        sources: [{ file: "<b>.md", text: markup, score: 1 }],
        mode: "stub",
      }),
    );
    const { container } = render(<AskForm />);

    submit();

    expect(await screen.findByText("<b>.md")).toBeTruthy();
    expect(screen.getAllByText(markup)).toHaveLength(2);
    expect(container.querySelector("img, b")).toBeNull();
  });

  async function expectError(message: string) {
    render(<AskForm />);
    submit();
    expect((await screen.findByRole("alert")).textContent).toBe(message);
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(button().disabled).toBe(false);
  }

  it("shows a string error detail", async () => {
    fetchMock.mockResolvedValue(Response.json({ detail: "LLM request failed" }, { status: 502 }));

    await expectError("LLM request failed");
  });

  it.each([
    ["a non-string detail", Response.json({ detail: [{ msg: "bad" }] }, { status: 422 }), 422],
    ["no detail", Response.json({}, { status: 500 }), 500],
    ["a null body", Response.json(null, { status: 503 }), 503],
    ["a non-JSON body", new Response("oops", { status: 504 }), 504],
    ["an unreadable success body", new Response("oops", { status: 200 }), 200],
  ])("shows the status for %s", async (_name, response, status) => {
    fetchMock.mockResolvedValue(response);

    await expectError(`Request failed (${status})`);
  });

  it("shows Request failed on a network failure", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    await expectError("Request failed");
  });

  it("clears the previous answer and error on a new submission", async () => {
    fetchMock.mockResolvedValueOnce(Response.json(ANSWER));
    fetchMock.mockResolvedValueOnce(Response.json({ detail: "Broken" }, { status: 502 }));
    fetchMock.mockReturnValueOnce(new Promise(() => {}));
    render(<AskForm />);

    submit("first");
    await screen.findByText(ANSWER.answer);

    submit("second");
    expect(screen.queryByText(ANSWER.answer)).toBeNull();
    expect(screen.queryByText("tea.md")).toBeNull();
    await screen.findByText("Broken");

    submit("third");
    expect(screen.queryByText("Broken")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
