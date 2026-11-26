// @vitest-environment jsdom
import {
  cleanup,
  configure,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AskForm from "../components/AskForm";
import type { AskResponse } from "../lib/types";

const fetchMock = vi.fn<typeof fetch>();

// findBy* and waitFor default to one second, too short on a busy machine.
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
  answer: "The Moon orbits the Earth.",
  sources: [
    { file: "moon.md", text: "# Moon\nIt orbits.", score: 2.3456 },
    { file: "sky.txt", text: "The sky is wide.", score: 1 },
  ],
  mode: "stub",
};

const input = () => screen.getByLabelText<HTMLInputElement>("Question");
const button = () => screen.getByRole<HTMLButtonElement>("button", { name: "Ask" });

function type(value: string): void {
  fireEvent.change(input(), { target: { value } });
}

// jsdom, like a browser, submits the form when its submit button is clicked.
function submit(value = "Where is the Moon?"): void {
  type(value);
  fireEvent.click(button());
}

function reply(status: number, body: unknown): void {
  fetchMock.mockResolvedValueOnce(Response.json(body, { status }));
}

function pendingReply(): (response: Response) => void {
  let resolve!: (response: Response) => void;
  fetchMock.mockReturnValueOnce(new Promise((done) => (resolve = done)));
  return resolve;
}

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
    type(" moon ");
    expect(button().disabled).toBe(false);
  });

  it("submits with Enter, through the form", async () => {
    render(<AskForm />);
    reply(200, ANSWER);
    type("moon");

    // Enter in a text input submits its form; the button must be a submitter.
    expect(button().type).toBe("submit");
    fireEvent.submit(input().closest("form")!);

    await screen.findByText(ANSWER.answer);
  });

  it("does not send an empty question even if the form is submitted", () => {
    render(<AskForm />);
    type("  ");

    fireEvent.submit(input().closest("form")!);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts the question to /api/ask", async () => {
    render(<AskForm />);
    reply(200, ANSWER);

    submit("  Where is the Moon?  ");
    await screen.findByText(ANSWER.answer);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(JSON.parse(String(init?.body))).toEqual({
      question: "Where is the Moon?",
    });
  });

  it("is disabled and shows Thinking… while waiting", async () => {
    render(<AskForm />);
    const resolve = pendingReply();
    expect(screen.queryByText("Thinking…")).toBeNull();

    submit();

    expect(button().disabled).toBe(true);
    expect(screen.getByText("Thinking…")).toBeTruthy();
    fireEvent.submit(input().closest("form")!);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    resolve(Response.json(ANSWER));
    await screen.findByText(ANSWER.answer);
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(button().disabled).toBe(false);
  });

  it("renders the answer and every source with a two-decimal score", async () => {
    render(<AskForm />);
    reply(200, ANSWER);

    submit();

    await screen.findByText(ANSWER.answer);
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("moon.md");
    expect(items[0].textContent).toContain("2.35");
    expect(items[0].textContent).toContain("# Moon\nIt orbits.");
    expect(items[1].textContent).toContain("sky.txt");
    expect(items[1].textContent).toContain("1.00");
    expect(items[1].textContent).toContain("The sky is wide.");
    expect(screen.queryByText("No matching sources.")).toBeNull();
  });

  it("renders returned text as plain text", async () => {
    const markup = '<img src="x" onerror="alert(1)">';
    const { container } = render(<AskForm />);
    reply(200, {
      answer: `${markup} **bold**`,
      sources: [{ file: "<b>f</b>.md", text: markup, score: 1 }],
      mode: "proxy",
    });

    submit();

    await screen.findByText(`${markup} **bold**`);
    expect(container.querySelector("img, b")).toBeNull();
  });

  it("says when there are no sources", async () => {
    render(<AskForm />);
    reply(200, { answer: "Nothing found.", sources: [], mode: "stub" });

    submit();

    await screen.findByText("Nothing found.");
    expect(screen.getByText("No matching sources.")).toBeTruthy();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  it("shows a string error detail", async () => {
    render(<AskForm />);
    reply(502, { detail: "LLM request failed" });

    submit();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("LLM request failed");
    expect(button().disabled).toBe(false);
  });

  it.each([
    ["a list detail", Response.json({ detail: [{ msg: "x" }] }, { status: 422 })],
    ["no detail", Response.json({}, { status: 422 })],
    ["a null body", Response.json(null, { status: 422 })],
    ["a non-JSON body", new Response("oops", { status: 422 })],
  ])("shows the status for %s", async (_name, response) => {
    render(<AskForm />);
    fetchMock.mockResolvedValueOnce(response);

    submit();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Request failed (422)");
  });

  it("shows a message on network failure", async () => {
    render(<AskForm />);
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    submit();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Request failed");
    expect(button().disabled).toBe(false);
    expect(screen.queryByText("Thinking…")).toBeNull();
  });

  it("clears the previous answer when submitting again", async () => {
    render(<AskForm />);
    reply(200, ANSWER);
    submit();
    await screen.findByText(ANSWER.answer);

    pendingReply();
    submit("another");

    expect(screen.queryByText(ANSWER.answer)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Sources" })).toBeNull();
  });

  it("clears the previous error when submitting again", async () => {
    render(<AskForm />);
    reply(502, { detail: "LLM request failed" });
    submit();
    await screen.findByRole("alert");

    reply(200, ANSWER);
    submit("another");

    expect(screen.queryByRole("alert")).toBeNull();
    await waitFor(() => expect(screen.getByText(ANSWER.answer)).toBeTruthy());
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
