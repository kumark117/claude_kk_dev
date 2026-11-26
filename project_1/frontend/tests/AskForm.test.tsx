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
  answer: "[stub] Based on cats.md: Cats purr.",
  sources: [
    { file: "cats.md", text: "Cats purr.", score: 1.23456 },
    { file: "dogs.txt", text: "Dogs bark.", score: 0.5 },
  ],
  mode: "stub",
};

function input() {
  return screen.getByLabelText<HTMLInputElement>("Question");
}

function button() {
  return screen.getByRole<HTMLButtonElement>("button", { name: "Ask" });
}

function type(value: string) {
  fireEvent.change(input(), { target: { value } });
}

function submit(value = "Do cats purr?") {
  type(value);
  fireEvent.click(button());
}

function reply(body: unknown, status = 200) {
  fetchMock.mockImplementationOnce(async () => Response.json(body, { status }));
}

/** Make the next request hang until the returned function is called. */
function holdReply() {
  let resolve!: (response: Response) => void;
  fetchMock.mockImplementationOnce(
    () => new Promise<Response>((done) => (resolve = done)),
  );
  return (body: unknown) => resolve(Response.json(body));
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
  it("has a labelled text input limited to 1000 characters", () => {
    expect(input().type).toBe("text");
    expect(input().maxLength).toBe(1000);
  });

  it("submits from the input through a form, so that Enter works", async () => {
    reply(ANSWER);
    type("Do cats purr?");
    expect(button().type).toBe("submit");
    expect(button().form).toBe(input().form);

    fireEvent.submit(input().form!);

    expect(await screen.findByText(ANSWER.answer)).toBeTruthy();
  });

  it("disables the button while the trimmed input is empty", () => {
    expect(button().disabled).toBe(true);
    type("   ");
    expect(button().disabled).toBe(true);
    type(" a ");
    expect(button().disabled).toBe(false);
  });

  it("does not send a blank question", () => {
    type("   ");

    fireEvent.submit(input().form!);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts the question to /api/ask", async () => {
    reply(ANSWER);

    submit("  Do cats purr?  ");

    await screen.findByText(ANSWER.answer);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(JSON.parse(init?.body as string)).toEqual({
      question: "Do cats purr?",
    });
  });

  it("is disabled and shows Thinking… while a request is in flight", async () => {
    const finish = holdReply();
    expect(screen.queryByText("Thinking…")).toBeNull();

    submit();

    expect(screen.getByText("Thinking…")).toBeTruthy();
    expect(button().disabled).toBe(true);
    type("another question");
    expect(button().disabled).toBe(true);
    fireEvent.submit(input().form!);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    finish(ANSWER);

    await screen.findByText(ANSWER.answer);
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(button().disabled).toBe(false);
  });

  it("shows the answer and each source with a two-decimal score", async () => {
    reply(ANSWER);

    submit();

    expect(await screen.findByText(ANSWER.answer)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("cats.md");
    expect(items[0].textContent).toContain("1.23");
    expect(items[0].textContent).not.toContain("1.234");
    expect(items[0].textContent).toContain("Cats purr.");
    expect(items[1].textContent).toContain("dogs.txt");
    expect(items[1].textContent).toContain("0.50");
    expect(items[1].textContent).toContain("Dogs bark.");
    expect(screen.queryByText("No matching sources.")).toBeNull();
  });

  it("says when there are no sources", async () => {
    reply({ answer: "Nothing found.", sources: [], mode: "stub" });

    submit();

    expect(await screen.findByText("No matching sources.")).toBeTruthy();
    expect(screen.getByText("Nothing found.")).toBeTruthy();
    expect(screen.queryByRole("listitem")).toBeNull();
  });

  it("renders returned text as plain text", async () => {
    const markup = '<img src="x"> **bold**';
    reply({
      answer: markup,
      sources: [{ file: "<b>.md", text: "<script>x</script>", score: 1 }],
      mode: "proxy",
    });

    submit();

    expect(await screen.findByText(markup)).toBeTruthy();
    expect(screen.getByText("<b>.md")).toBeTruthy();
    expect(screen.getByText("<script>x</script>")).toBeTruthy();
    expect(document.querySelector("img, script, b")).toBeNull();
  });

  it("shows a string error detail", async () => {
    reply({ detail: "Backend unavailable" }, 502);

    submit();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Backend unavailable");
    expect(button().disabled).toBe(false);
  });

  it.each([
    ["a list detail", JSON.stringify({ detail: [{ msg: "bad" }] }), 422],
    ["no detail", JSON.stringify({}), 500],
    ["a null body", "null", 500],
    ["a body that is not JSON", "<h1>oops</h1>", 503],
  ])("shows the status for %s", async (_name, body, status) => {
    fetchMock.mockImplementationOnce(
      async () => new Response(body, { status }),
    );

    submit();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(`Request failed (${status})`);
  });

  it("shows Request failed on a network failure", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    submit();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Request failed");
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(button().disabled).toBe(false);
  });

  it("clears the previous answer on a new submission", async () => {
    reply(ANSWER);
    submit();
    await screen.findByText(ANSWER.answer);
    holdReply();

    submit("again");

    expect(screen.queryByText(ANSWER.answer)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Sources" })).toBeNull();
  });

  it("clears the previous error on a new submission", async () => {
    reply({ detail: "LLM request failed" }, 502);
    submit();
    await screen.findByRole("alert");
    const finish = holdReply();

    submit("again");

    expect(screen.queryByRole("alert")).toBeNull();
    finish(ANSWER);
    await waitFor(() => expect(screen.getByText(ANSWER.answer)).toBeTruthy());
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
