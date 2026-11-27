// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AskForm from "../components/AskForm";

const fetchMock = vi.fn<typeof fetch>();

const success = {
  answer: "[stub] Based on kettle.md: The kettle boils water.",
  sources: [
    { file: "kettle.md", text: "The kettle boils water.", score: 1.2345 },
    { file: "teapot.txt", text: "<b>Teapots</b> steep tea.", score: 0.5 },
  ],
  mode: "stub",
};

const input = () => screen.getByLabelText<HTMLInputElement>("Question");
const button = () => screen.getByRole<HTMLButtonElement>("button", { name: "Ask" });
const type = (value: string) =>
  fireEvent.change(input(), { target: { value } });

function submit(value = "What boils water?") {
  type(value);
  fireEvent.click(button());
}

function deferred() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => (resolve = done));
  return { promise, resolve };
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

  it("submits through a form, so Enter works", async () => {
    fetchMock.mockResolvedValue(Response.json(success));
    expect(button().type).toBe("submit");
    type("kettle");

    fireEvent.submit(input().closest("form")!);

    expect(await screen.findByText(success.answer)).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("disables the button while the trimmed input is empty", () => {
    expect(button().disabled).toBe(true);
    type("   ");
    expect(button().disabled).toBe(true);
    fireEvent.submit(input().closest("form")!);
    expect(fetchMock).not.toHaveBeenCalled();
    type("kettle");
    expect(button().disabled).toBe(false);
  });

  it("posts the question to /api/ask", async () => {
    fetchMock.mockResolvedValue(Response.json(success));

    submit("  kettle  ");
    await screen.findByText(success.answer);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(JSON.parse(String(init?.body))).toEqual({ question: "kettle" });
  });

  it("disables the button and shows Thinking… while waiting", async () => {
    const pending = deferred();
    fetchMock.mockReturnValue(pending.promise);
    expect(screen.queryByText("Thinking…")).toBeNull();

    submit();

    expect(button().disabled).toBe(true);
    expect(screen.getByText("Thinking…")).toBeTruthy();
    fireEvent.submit(input().closest("form")!);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    pending.resolve(Response.json(success));
    await screen.findByText(success.answer);
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(button().disabled).toBe(false);
  });

  it("renders the answer and each source as plain text", async () => {
    fetchMock.mockResolvedValue(Response.json(success));

    submit();

    expect(await screen.findByText(success.answer)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("kettle.md");
    expect(items[0].textContent).toContain("1.23");
    expect(items[0].textContent).not.toContain("1.234");
    expect(items[0].textContent).toContain("The kettle boils water.");
    expect(items[1].textContent).toContain("teapot.txt");
    expect(items[1].textContent).toContain("0.50");
    expect(items[1].textContent).toContain("<b>Teapots</b> steep tea.");
    expect(items[1].querySelector("b")).toBeNull();
    expect(screen.queryByText("No matching sources.")).toBeNull();
  });

  it("says when there are no matching sources", async () => {
    fetchMock.mockResolvedValue(
      Response.json({ answer: "Nothing found.", sources: [], mode: "stub" }),
    );

    submit();

    expect(await screen.findByText("Nothing found.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    expect(screen.getByText("No matching sources.")).toBeTruthy();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  it("shows a string error detail", async () => {
    fetchMock.mockResolvedValue(
      Response.json({ detail: "LLM request failed" }, { status: 502 }),
    );

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "LLM request failed",
    );
    expect(screen.queryByRole("heading", { name: "Sources" })).toBeNull();
    expect(button().disabled).toBe(false);
  });

  it.each([
    ["an array detail", JSON.stringify({ detail: [{ msg: "bad" }] }), 422],
    ["no detail", JSON.stringify({}), 500],
    ["a null body", "null", 500],
    ["a non-JSON body", "<html></html>", 503],
  ])("falls back to the status for %s", async (_name, body, status) => {
    fetchMock.mockResolvedValue(new Response(body, { status }));

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      `Request failed (${status})`,
    );
  });

  it("treats an unreadable success reply as a failure", async () => {
    fetchMock.mockResolvedValue(new Response("<html></html>", { status: 200 }));

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Request failed (200)",
    );
  });

  it("reports a network failure", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Request failed",
    );
    expect(button().disabled).toBe(false);
  });

  it("clears the previous answer on a new submission", async () => {
    fetchMock.mockResolvedValueOnce(Response.json(success));
    submit();
    await screen.findByText(success.answer);
    const pending = deferred();
    fetchMock.mockReturnValueOnce(pending.promise);

    submit("another");

    expect(screen.queryByText(success.answer)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Sources" })).toBeNull();
    pending.resolve(Response.json({ detail: "nope" }, { status: 502 }));
    await screen.findByRole("alert");
  });

  it("clears the previous error on a new submission", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    submit();
    await screen.findByRole("alert");
    const pending = deferred();
    fetchMock.mockReturnValueOnce(pending.promise);

    submit("another");

    expect(screen.queryByRole("alert")).toBeNull();
    pending.resolve(Response.json(success));
    await screen.findByText(success.answer);
  });
});
