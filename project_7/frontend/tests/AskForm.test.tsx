// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Page from "../app/page";
import AskForm from "../components/AskForm";

const fetchMock = vi.fn<typeof fetch>();

const ANSWER = {
  answer: "[stub] Based on cats.md: Cats purr.",
  sources: [
    { file: "cats.md", text: "Cats purr.", score: 1.2345 },
    { file: "dogs.txt", text: "Dogs <b>bark</b>.", score: 0.5 },
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
const type = (value: string) => fireEvent.change(input(), { target: { value } });

/** Type a question, submit the form and let the request settle. */
async function submit(question = "Do cats purr?") {
  type(question);
  await act(async () => {
    fireEvent.click(button());
  });
}

/** A fetch reply that stays pending until resolved by the test. */
function deferred() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  fetchMock.mockReturnValueOnce(promise);
  return resolve;
}

describe("page", () => {
  it("shows the heading and the form", () => {
    render(<Page />);

    expect(screen.getByRole("heading", { name: "Ask the docs" })).toBeTruthy();
    expect(input().maxLength).toBe(1000);
    expect(input().type).toBe("text");
    expect(button().type).toBe("submit");
  });
});

describe("AskForm", () => {
  it("disables the button while the trimmed input is empty", () => {
    render(<AskForm />);

    expect(button().disabled).toBe(true);
    type("   ");
    expect(button().disabled).toBe(true);
    type("cats");
    expect(button().disabled).toBe(false);
  });

  it("does not send a blank question when the form is submitted", () => {
    render(<AskForm />);
    type("   ");

    fireEvent.submit(input().form!);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts the trimmed question on form submit (Enter)", async () => {
    fetchMock.mockResolvedValue(Response.json(ANSWER));
    render(<AskForm />);
    type("  Do cats purr?  ");

    await act(async () => {
      fireEvent.submit(input().form!);
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/ask");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ question: "Do cats purr?" });
  });

  it("disables the button and shows Thinking… while loading", async () => {
    const resolve = deferred();
    render(<AskForm />);
    expect(screen.queryByText("Thinking…")).toBeNull();

    await submit();

    expect(button().disabled).toBe(true);
    expect(screen.getByText("Thinking…")).toBeTruthy();
    fireEvent.submit(input().form!);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await act(async () => resolve(Response.json(ANSWER)));

    expect(button().disabled).toBe(false);
    expect(screen.queryByText("Thinking…")).toBeNull();
  });

  it("renders the answer and sources as plain text", async () => {
    fetchMock.mockResolvedValue(Response.json(ANSWER));
    const { container } = render(<AskForm />);

    await submit();

    expect(screen.getByText(ANSWER.answer)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    const items = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(items).toHaveLength(2);
    expect(items[0]).toContain("cats.md");
    expect(items[0]).toContain("1.23");
    expect(items[0]).not.toContain("1.2345");
    expect(items[0]).toContain("Cats purr.");
    expect(items[1]).toContain("dogs.txt");
    expect(items[1]).toContain("0.50");
    expect(items[1]).toContain("Dogs <b>bark</b>.");
    expect(container.querySelector("b")).toBeNull();
    expect(screen.queryByText("No matching sources.")).toBeNull();
  });

  it("says when there are no matching sources", async () => {
    fetchMock.mockResolvedValue(
      Response.json({ answer: "Nothing found.", sources: [], mode: "stub" }),
    );
    render(<AskForm />);

    await submit();

    expect(screen.getByText("Nothing found.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    expect(screen.getByText("No matching sources.")).toBeTruthy();
    expect(screen.queryByRole("listitem")).toBeNull();
  });

  it("shows a string error detail", async () => {
    fetchMock.mockResolvedValue(
      Response.json({ detail: "Backend unavailable" }, { status: 502 }),
    );
    render(<AskForm />);

    await submit();

    expect(screen.getByRole("alert").textContent).toBe("Backend unavailable");
  });

  it.each([
    ["a non-string detail", Response.json({ detail: [{ msg: "bad" }] }, { status: 422 }), 422],
    ["no detail", Response.json({}, { status: 500 }), 500],
    ["a null body", Response.json(null, { status: 503 }), 503],
    ["a non-JSON body", new Response("oops", { status: 504 }), 504],
    ["a non-JSON success", new Response("oops", { status: 200 }), 200],
  ])("shows the status for %s", async (_name, reply, status) => {
    fetchMock.mockResolvedValue(reply);
    render(<AskForm />);

    await submit();

    expect(screen.getByRole("alert").textContent).toBe(`Request failed (${status})`);
    expect(button().disabled).toBe(false);
  });

  it("reports a network failure", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    render(<AskForm />);

    await submit();

    expect(screen.getByRole("alert").textContent).toBe("Request failed");
    expect(button().disabled).toBe(false);
  });

  it("clears the previous answer on a new submission", async () => {
    fetchMock.mockResolvedValueOnce(Response.json(ANSWER));
    render(<AskForm />);
    await submit();
    deferred();

    await submit("Another question");

    expect(screen.queryByText(ANSWER.answer)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Sources" })).toBeNull();
  });

  it("clears the previous error on a new submission", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    render(<AskForm />);
    await submit();
    deferred();

    await submit("Another question");

    expect(screen.queryByRole("alert")).toBeNull();
  });
});
