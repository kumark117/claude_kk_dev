// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AskForm from "../components/AskForm";

const fetchMock = vi.fn<typeof fetch>();

const success = {
  answer: "Lava is molten rock.",
  sources: [
    { file: "lava.md", text: "<b>Lava</b> is molten rock.", score: 1.23456 },
    { file: "ash.txt", text: "Ash travels far.", score: 0.5 },
  ],
  mode: "stub",
};

function input() {
  return screen.getByLabelText("Question") as HTMLInputElement;
}

function button() {
  return screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement;
}

function type(value: string) {
  fireEvent.change(input(), { target: { value } });
}

function submit(value = "What is lava?") {
  type(value);
  fireEvent.click(button());
}

function reply(body: unknown, status = 200) {
  fetchMock.mockImplementationOnce(async () => Response.json(body, { status }));
}

/** A fetch call that stays pending until the returned function is called. */
function holdReply() {
  let release!: (response: Response) => void;
  fetchMock.mockImplementationOnce(
    () => new Promise<Response>((resolve) => (release = resolve)),
  );
  return (body: unknown) => release(Response.json(body));
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
    type(" lava ");
    expect(button().disabled).toBe(false);
  });

  it("submits through the form, so Enter works", async () => {
    reply(success);
    type("lava");

    expect(button().type).toBe("submit");
    fireEvent.submit(input().closest("form")!);

    await screen.findByText(success.answer);
  });

  it("ignores a form submission with an empty input", () => {
    type("   ");
    fireEvent.submit(input().closest("form")!);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts the question to /api/ask", async () => {
    reply(success);

    submit("  What is lava?  ");
    await screen.findByText(success.answer);

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

  it("disables the button and shows Thinking… while loading", async () => {
    const release = holdReply();

    submit();

    expect(button().disabled).toBe(true);
    expect(screen.getByText("Thinking…")).toBeTruthy();
    fireEvent.submit(input().closest("form")!);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    release(success);
    await screen.findByText(success.answer);
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(button().disabled).toBe(false);
  });

  it("renders the answer and sources as plain text", async () => {
    reply(success);

    submit();
    await screen.findByText(success.answer);

    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("lava.md");
    expect(items[0].textContent).toContain("1.23");
    expect(items[0].textContent).not.toContain("1.234");
    expect(items[0].textContent).toContain("<b>Lava</b> is molten rock.");
    expect(items[0].querySelector("b")).toBeNull();
    expect(items[1].textContent).toContain("ash.txt");
    expect(items[1].textContent).toContain("0.50");
    expect(items[1].textContent).toContain("Ash travels far.");
    expect(screen.queryByText("No matching sources.")).toBeNull();
  });

  it("shows a message when there are no sources", async () => {
    reply({ answer: "Nothing found.", sources: [], mode: "stub" });

    submit();
    await screen.findByText("Nothing found.");

    expect(screen.getByText("No matching sources.")).toBeTruthy();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  it("shows a string error detail", async () => {
    reply({ detail: "Backend unavailable" }, 502);

    submit();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Backend unavailable");
    expect(button().disabled).toBe(false);
  });

  it.each([
    ["a list detail", Response.json({ detail: [{ msg: "x" }] }, { status: 422 })],
    ["no detail", Response.json({}, { status: 422 })],
    ["a null body", Response.json(null, { status: 422 })],
    ["a non-JSON body", new Response("oops", { status: 422 })],
  ])("shows the status for %s", async (_, response) => {
    fetchMock.mockResolvedValueOnce(response);

    submit();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Request failed (422)");
  });

  it("shows Request failed on a network failure", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));

    submit();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Request failed");
    expect(button().disabled).toBe(false);
  });

  it("clears the previous answer on a new submission", async () => {
    reply(success);
    submit();
    await screen.findByText(success.answer);
    const release = holdReply();

    submit("another question");

    expect(screen.queryByText(success.answer)).toBeNull();
    expect(screen.queryByText("lava.md")).toBeNull();
    release({ answer: "Second.", sources: [], mode: "stub" });
    await screen.findByText("Second.");
  });

  it("clears the previous error on a new submission", async () => {
    reply({ detail: "LLM request failed" }, 502);
    submit();
    await screen.findByRole("alert");
    const release = holdReply();

    submit("another question");

    expect(screen.queryByRole("alert")).toBeNull();
    release(success);
    await screen.findByText(success.answer);
  });
});
