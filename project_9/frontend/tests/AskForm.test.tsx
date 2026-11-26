import {
  cleanup,
  configure,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AskForm from "../components/AskForm";
import Page from "../app/page";

const fetchMock = vi.fn<typeof fetch>();

// How long findBy* queries wait; the default of 1 s is short on a busy machine.
configure({ asyncUtilTimeout: 10_000 });

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

const success = {
  answer: "Penguins huddle.",
  sources: [
    { file: "birds.md", text: "Penguins huddle to keep warm.", score: 2.345678 },
    { file: "sea.txt", text: "The tide turns.", score: 1 },
  ],
  mode: "stub",
};

const input = () => screen.getByLabelText<HTMLInputElement>("Question");
const button = () => screen.getByRole<HTMLButtonElement>("button", { name: "Ask" });

function type(value: string) {
  fireEvent.change(input(), { target: { value } });
}

function submit(value = "penguins") {
  type(value);
  fireEvent.click(button());
}

function pending() {
  let resolve!: (response: Response) => void;
  fetchMock.mockReturnValueOnce(
    new Promise<Response>((done) => {
      resolve = done;
    }),
  );
  return (response: Response) => resolve(response);
}

describe("Page", () => {
  it("has the heading and the form", () => {
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
  });

  it("disables the button while the trimmed input is empty", () => {
    render(<AskForm />);
    expect(button().disabled).toBe(true);

    type("   ");
    expect(button().disabled).toBe(true);

    type("penguins");
    expect(button().disabled).toBe(false);
  });

  it("does not send an empty question when the form is submitted", () => {
    render(<AskForm />);
    type("   ");

    fireEvent.submit(input().form!);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts the trimmed question when the form is submitted", async () => {
    fetchMock.mockResolvedValue(Response.json(success));
    render(<AskForm />);
    type("  penguins  ");

    // Pressing Enter in a form input submits the form.
    fireEvent.submit(input().form!);

    await screen.findByText("Penguins huddle.");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/ask");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ question: "penguins" });
  });

  it("uses a submit button so that Enter submits", () => {
    render(<AskForm />);

    expect(button().type).toBe("submit");
    expect(button().form).toBe(input().form);
  });

  it("is disabled and shows Thinking… while loading", async () => {
    const respond = pending();
    render(<AskForm />);
    expect(screen.queryByText("Thinking…")).toBeNull();

    submit();

    expect(screen.getByText("Thinking…")).toBeTruthy();
    expect(button().disabled).toBe(true);
    fireEvent.submit(input().form!);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    respond(Response.json(success));
    await screen.findByText("Penguins huddle.");
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(button().disabled).toBe(false);
  });

  it("renders the answer and the sources with two-decimal scores", async () => {
    fetchMock.mockResolvedValue(Response.json(success));
    render(<AskForm />);

    submit();

    await screen.findByText("Penguins huddle.");
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    const items = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(items).toHaveLength(2);
    expect(items[0]).toContain("birds.md");
    expect(items[0]).toContain("2.35");
    expect(items[0]).toContain("Penguins huddle to keep warm.");
    expect(items[1]).toContain("sea.txt");
    expect(items[1]).toContain("1.00");
    expect(screen.queryByText("No matching sources.")).toBeNull();
  });

  it("renders returned text as plain text", async () => {
    const markup = '<img src="x" alt="injected">';
    fetchMock.mockResolvedValue(
      Response.json({
        answer: markup,
        sources: [{ file: "<b>.md", text: "**bold**", score: 1 }],
        mode: "stub",
      }),
    );
    render(<AskForm />);

    submit();

    await screen.findByText(markup);
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByText("**bold**")).toBeTruthy();
    expect(screen.getByText("<b>.md")).toBeTruthy();
  });

  it("shows a message when there are no sources", async () => {
    fetchMock.mockResolvedValue(
      Response.json({ answer: "Nothing found.", sources: [], mode: "stub" }),
    );
    render(<AskForm />);

    submit();

    await screen.findByText("Nothing found.");
    expect(screen.getByText("No matching sources.")).toBeTruthy();
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
    ["an array detail", Response.json({ detail: [{ msg: "bad" }] }, { status: 422 }), 422],
    ["no detail", Response.json({}, { status: 500 }), 500],
    ["a null body", Response.json(null, { status: 503 }), 503],
    ["a non-JSON body", new Response("oops", { status: 504 }), 504],
    ["an unreadable success body", new Response("oops", { status: 200 }), 200],
  ])("shows the status for %s", async (_name, response, status) => {
    fetchMock.mockResolvedValue(response);
    render(<AskForm />);

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      `Request failed (${status})`,
    );
  });

  it("shows a generic message on network failure", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    render(<AskForm />);

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe("Request failed");
    expect(button().disabled).toBe(false);
  });

  it("clears the previous answer on a new submission", async () => {
    fetchMock.mockResolvedValueOnce(Response.json(success));
    render(<AskForm />);
    submit();
    await screen.findByText("Penguins huddle.");

    const respond = pending();
    submit("tides");

    expect(screen.queryByText("Penguins huddle.")).toBeNull();
    expect(screen.queryByRole("listitem")).toBeNull();
    respond(Response.json({ detail: "LLM request failed" }, { status: 502 }));
    await screen.findByText("LLM request failed");
  });

  it("clears the previous error on a new submission", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    render(<AskForm />);
    submit();
    await screen.findByRole("alert");

    const respond = pending();
    submit("tides");

    expect(screen.queryByRole("alert")).toBeNull();
    respond(Response.json(success));
    await screen.findByText("Penguins huddle.");
  });
});
