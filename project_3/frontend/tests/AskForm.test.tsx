// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import AskForm from "../components/AskForm";

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

const input = () => screen.getByLabelText<HTMLInputElement>("Question");
const button = () => screen.getByRole<HTMLButtonElement>("button", { name: "Ask" });

function type(value: string) {
  fireEvent.change(input(), { target: { value } });
}

function submit(value = "What is alpha?") {
  type(value);
  fireEvent.click(button());
}

const success = {
  answer: "Alpha is first.",
  sources: [
    { file: "alpha.md", text: "Alpha is the first letter.", score: 1.23456 },
    { file: "beta.txt", text: "Beta follows alpha.", score: 0.5 },
  ],
  mode: "stub",
};

describe("AskForm", () => {
  test("has a labelled input limited to 1000 characters", () => {
    render(<AskForm />);

    expect(input().type).toBe("text");
    expect(input().maxLength).toBe(1000);
  });

  test("disables the button while the trimmed input is empty", () => {
    render(<AskForm />);
    expect(button().disabled).toBe(true);

    type("   ");
    expect(button().disabled).toBe(true);

    type(" a ");
    expect(button().disabled).toBe(false);
  });

  test("does not send a blank question when the form is submitted", () => {
    render(<AskForm />);
    type("   ");

    fireEvent.submit(input().closest("form")!);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("submits through the form, which is what Enter does", async () => {
    fetchMock.mockResolvedValue(Response.json(success));
    render(<AskForm />);
    type("  What is alpha?  ");

    expect(button().type).toBe("submit");
    fireEvent.submit(input().closest("form")!);

    await screen.findByText("Alpha is first.");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe("application/json");
    expect(JSON.parse(init?.body as string)).toEqual({
      question: "What is alpha?",
    });
  });

  test("disables the button and shows Thinking… while loading", async () => {
    let resolve!: (response: Response) => void;
    fetchMock.mockReturnValue(new Promise((done) => (resolve = done)));
    render(<AskForm />);
    expect(screen.queryByText("Thinking…")).toBeNull();

    submit();

    expect(button().disabled).toBe(true);
    expect(screen.getByText("Thinking…")).toBeTruthy();
    fireEvent.submit(input().closest("form")!);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    resolve(Response.json(success));
    await screen.findByText("Alpha is first.");
    expect(button().disabled).toBe(false);
    expect(screen.queryByText("Thinking…")).toBeNull();
  });

  test("renders the answer and each source", async () => {
    fetchMock.mockResolvedValue(Response.json(success));
    render(<AskForm />);

    submit();

    await screen.findByText("Alpha is first.");
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("alpha.md");
    expect(items[0].textContent).toContain("1.23");
    expect(items[0].textContent).not.toContain("1.234");
    expect(items[0].textContent).toContain("Alpha is the first letter.");
    expect(items[1].textContent).toContain("beta.txt");
    expect(items[1].textContent).toContain("0.50");
    expect(screen.queryByText("No matching sources.")).toBeNull();
  });

  test("renders returned text as plain text", async () => {
    const markup = '<img src=x onerror="alert(1)">';
    fetchMock.mockResolvedValue(
      Response.json({
        answer: `answer ${markup}`,
        sources: [{ file: "x.md", text: `source ${markup}`, score: 1 }],
        mode: "stub",
      }),
    );
    const { container } = render(<AskForm />);

    submit();

    await screen.findByText(`answer ${markup}`);
    expect(screen.getByText(`source ${markup}`)).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
  });

  test("shows the empty-sources message", async () => {
    fetchMock.mockResolvedValue(
      Response.json({ answer: "Nothing.", sources: [], mode: "stub" }),
    );
    render(<AskForm />);

    submit();

    await screen.findByText("No matching sources.");
    expect(screen.getByText("Nothing.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
  });

  async function expectError(message: string) {
    expect((await screen.findByRole("alert")).textContent).toBe(message);
    expect(button().disabled).toBe(false);
    expect(screen.queryByRole("heading", { name: "Sources" })).toBeNull();
  }

  test("shows a string error detail", async () => {
    fetchMock.mockResolvedValue(
      Response.json({ detail: "LLM request failed" }, { status: 502 }),
    );
    render(<AskForm />);

    submit();

    await expectError("LLM request failed");
  });

  test.each([
    ["a list detail", Response.json({ detail: [{ msg: "bad" }] }, { status: 422 }), 422],
    ["no detail", Response.json({}, { status: 500 }), 500],
    ["a null body", Response.json(null, { status: 500 }), 500],
    ["a non-JSON body", new Response("oops", { status: 503 }), 503],
  ])("shows the status for %s", async (_name, response, status) => {
    fetchMock.mockResolvedValue(response);
    render(<AskForm />);

    submit();

    await expectError(`Request failed (${status})`);
  });

  test("shows Request failed on a network failure", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    render(<AskForm />);

    submit();

    await expectError("Request failed");
  });

  test("clears the previous answer and error on a new submission", async () => {
    fetchMock.mockResolvedValueOnce(Response.json(success));
    render(<AskForm />);
    submit();
    await screen.findByText("Alpha is first.");

    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    submit("second");

    expect(screen.queryByText("Alpha is first.")).toBeNull();
    expect(screen.queryByText("alpha.md")).toBeNull();
    await screen.findByRole("alert");

    fetchMock.mockReturnValueOnce(new Promise(() => {}));
    submit("third");

    expect(screen.queryByRole("alert")).toBeNull();
  });
});
