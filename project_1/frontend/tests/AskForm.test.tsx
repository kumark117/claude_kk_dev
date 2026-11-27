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

const fetchMock = vi.fn<typeof fetch>();

const success = {
  answer: "Bees make honey.",
  sources: [
    { file: "bees.md", text: "Bees make honey from nectar.", score: 1.23456 },
    { file: "hives.txt", text: "A hive holds many bees.", score: 0.5 },
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
const button = () =>
  screen.getByRole<HTMLButtonElement>("button", { name: "Ask" });
const type = (value: string) =>
  fireEvent.change(input(), { target: { value } });

function submit(question = "Who makes honey?") {
  type(question);
  fireEvent.click(button());
}

function reply(body: unknown, status = 200) {
  fetchMock.mockImplementationOnce(async () =>
    Response.json(body, { status }),
  );
}

function pendingReply() {
  let resolve!: (response: Response) => void;
  fetchMock.mockImplementationOnce(
    () => new Promise<Response>((done) => (resolve = done)),
  );
  return (body: unknown) => resolve(Response.json(body));
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

    type(" honey ");
    expect(button().disabled).toBe(false);
  });

  it("does not send a blank question submitted with Enter", () => {
    render(<AskForm />);
    type("   ");

    fireEvent.submit(input().form!);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts the trimmed question when the form is submitted", async () => {
    reply(success);
    render(<AskForm />);
    type("  Who makes honey?  ");

    // Enter in a text input submits its form.
    expect(button().type).toBe("submit");
    fireEvent.submit(input().form!);

    await screen.findByText("Bees make honey.");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(JSON.parse(init?.body as string)).toEqual({
      question: "Who makes honey?",
    });
  });

  it("is disabled and shows Thinking… while waiting", async () => {
    const finish = pendingReply();
    render(<AskForm />);
    expect(screen.queryByText("Thinking…")).toBeNull();

    submit();

    expect(button().disabled).toBe(true);
    expect(screen.getByText("Thinking…")).toBeTruthy();
    fireEvent.submit(input().form!);
    expect(fetchMock).toHaveBeenCalledOnce();

    finish(success);
    await screen.findByText("Bees make honey.");
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(button().disabled).toBe(false);
  });

  it("shows the answer and each source with a two-decimal score", async () => {
    reply(success);
    render(<AskForm />);

    submit();

    await screen.findByText("Bees make honey.");
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    const items = screen.getAllByRole("listitem").map((i) => i.textContent);
    expect(items).toHaveLength(2);
    for (const part of ["bees.md", "1.23", "Bees make honey from nectar."]) {
      expect(items[0]).toContain(part);
    }
    expect(items[0]).not.toContain("1.234");
    for (const part of ["hives.txt", "0.50", "A hive holds many bees."]) {
      expect(items[1]).toContain(part);
    }
    expect(screen.queryByText("No matching sources.")).toBeNull();
  });

  it("says when there are no matching sources", async () => {
    reply({ answer: "Nothing found.", sources: [], mode: "stub" });
    render(<AskForm />);

    submit();

    await screen.findByText("No matching sources.");
    expect(screen.getByText("Nothing found.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  it("renders returned text as plain text", async () => {
    const markup = '<img src="x" alt="injected"> **bold**';
    reply({
      answer: markup,
      sources: [{ file: "a.md", text: "<b>source</b>", score: 1 }],
      mode: "proxy",
    });
    render(<AskForm />);

    submit();

    await screen.findByText(markup);
    expect(screen.getByText("<b>source</b>")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
  });

  async function expectError(message: string) {
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(message);
    await waitFor(() => expect(button().disabled).toBe(false));
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Sources" })).toBeNull();
  }

  it("shows a string error detail", async () => {
    reply({ detail: "Backend unavailable" }, 502);
    render(<AskForm />);

    submit();

    await expectError("Backend unavailable");
  });

  it.each([
    ["a list", { detail: [{ msg: "too long" }] }],
    ["missing", {}],
    ["in a null body", null],
  ])("shows the status when the detail is %s", async (_name, body) => {
    reply(body, 422);
    render(<AskForm />);

    submit();

    await expectError("Request failed (422)");
  });

  it("shows the status when an error reply is not JSON", async () => {
    fetchMock.mockImplementationOnce(
      async () => new Response("oops", { status: 500 }),
    );
    render(<AskForm />);

    submit();

    await expectError("Request failed (500)");
  });

  it("shows Request failed on a network failure", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    render(<AskForm />);

    submit();

    await expectError("Request failed");
  });

  it("clears the previous answer on a new submission", async () => {
    reply(success);
    render(<AskForm />);
    submit();
    await screen.findByText("Bees make honey.");

    const finish = pendingReply();
    submit("Another question");

    expect(screen.queryByText("Bees make honey.")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Sources" })).toBeNull();
    finish(success);
    await screen.findByText("Bees make honey.");
  });

  it("clears the previous error on a new submission", async () => {
    reply({ detail: "Backend unavailable" }, 502);
    render(<AskForm />);
    submit();
    await screen.findByRole("alert");

    const finish = pendingReply();
    submit("Another question");

    expect(screen.queryByRole("alert")).toBeNull();
    finish(success);
    await screen.findByText("Bees make honey.");
  });
});
