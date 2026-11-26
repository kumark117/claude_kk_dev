import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AskForm from "../components/AskForm";
import type { AskResponse } from "../lib/types";

const fetchMock = vi.fn<typeof fetch>();

const RESULT: AskResponse = {
  answer: "Lava is molten rock.",
  sources: [
    { file: "volcano.md", text: "Volcanoes erupt lava.", score: 2.345678 },
    { file: "rocks.txt", text: "Rocks are hard.", score: 1 },
  ],
  mode: "stub",
};

const input = () => screen.getByLabelText<HTMLInputElement>("Question");
const button = () => screen.getByRole<HTMLButtonElement>("button", { name: "Ask" });

function type(value: string) {
  fireEvent.change(input(), { target: { value } });
}

function submit(value = "What is lava?") {
  type(value);
  fireEvent.click(button());
}

function replyWith(body: unknown, status = 200) {
  fetchMock.mockImplementationOnce(async () => Response.json(body, { status }));
}

/** A request that stays in flight until resolve() is called. */
function replyLater() {
  let resolve!: (response: Response) => void;
  fetchMock.mockImplementationOnce(
    () => new Promise<Response>((r) => (resolve = r)),
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
  it("has a labelled text input limited to 1000 characters", () => {
    expect(input().type).toBe("text");
    expect(input().maxLength).toBe(1000);
  });

  it("disables the button while the trimmed input is empty", () => {
    expect(button().disabled).toBe(true);
    type("   ");
    expect(button().disabled).toBe(true);
    type(" x ");
    expect(button().disabled).toBe(false);
  });

  it("sends the question to /api/ask", async () => {
    replyWith(RESULT);

    submit("  What is lava?  ");
    await screen.findByText(RESULT.answer);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(JSON.parse(init?.body as string)).toEqual({
      question: "What is lava?",
    });
  });

  it("submits through the form, as Enter does", async () => {
    replyWith(RESULT);
    type("lava");

    // jsdom has no implicit submission; Enter submits the input's form.
    expect(button().type).toBe("submit");
    fireEvent.submit(input().form!);

    expect(await screen.findByText(RESULT.answer)).toBeTruthy();
  });

  it("does not submit an empty question through the form", () => {
    type("  ");
    fireEvent.submit(input().form!);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("disables the button and shows Thinking… while waiting", async () => {
    const resolve = replyLater();
    expect(screen.queryByText("Thinking…")).toBeNull();

    submit();

    expect(button().disabled).toBe(true);
    expect(screen.getByText("Thinking…")).toBeTruthy();
    fireEvent.submit(input().form!);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    resolve(RESULT);
    await screen.findByText(RESULT.answer);
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(button().disabled).toBe(false);
  });

  it("renders the answer and each source with a two-decimal score", async () => {
    replyWith(RESULT);

    submit();

    expect(await screen.findByText(RESULT.answer)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toHaveLength(2);
    expect(items[0]).toContain("volcano.md");
    expect(items[0]).toContain("2.35");
    expect(items[0]).not.toContain("2.345");
    expect(items[0]).toContain("Volcanoes erupt lava.");
    expect(items[1]).toContain("rocks.txt");
    expect(items[1]).toContain("1.00");
    expect(screen.queryByText("No matching sources.")).toBeNull();
  });

  it("shows a message when there are no sources", async () => {
    replyWith({ answer: "Nothing found.", sources: [], mode: "stub" });

    submit();

    expect(await screen.findByText("No matching sources.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  it("renders returned text as plain text", async () => {
    const markup = '<img src="x"> **bold**';
    replyWith({
      answer: markup,
      sources: [{ file: "<b>f</b>.md", text: "<script>x</script>", score: 1 }],
      mode: "proxy",
    });

    submit();

    expect(await screen.findByText(markup)).toBeTruthy();
    expect(screen.getByText("<b>f</b>.md")).toBeTruthy();
    expect(screen.getByText("<script>x</script>")).toBeTruthy();
    expect(document.querySelector("img, b, script")).toBeNull();
  });

  it("shows a string error detail", async () => {
    replyWith({ detail: "LLM request failed" }, 502);

    submit();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("LLM request failed");
    expect(button().disabled).toBe(false);
  });

  it.each([
    ["a list detail", Response.json({ detail: [{ msg: "x" }] }, { status: 422 }), 422],
    ["no detail", Response.json({}, { status: 500 }), 500],
    ["a null body", Response.json(null, { status: 500 }), 500],
    ["a non-JSON body", new Response("oops", { status: 503 }), 503],
  ])("shows the status for %s", async (_name, response, status) => {
    fetchMock.mockResolvedValueOnce(response);

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
    replyWith(RESULT);
    submit();
    await screen.findByText(RESULT.answer);
    const resolve = replyLater();

    submit("again");

    expect(screen.queryByText(RESULT.answer)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Sources" })).toBeNull();
    resolve(RESULT);
    await screen.findByText(RESULT.answer);
  });

  it("clears the previous error on a new submission", async () => {
    replyWith({ detail: "LLM request failed" }, 502);
    submit();
    await screen.findByRole("alert");
    const resolve = replyLater();

    submit("again");

    expect(screen.queryByRole("alert")).toBeNull();
    resolve(RESULT);
    await screen.findByText(RESULT.answer);
  });
});
