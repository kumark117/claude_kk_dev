// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Page from "../app/page";
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

const ANSWER = {
  answer: "Rockets burn fuel.",
  sources: [
    { file: "rockets.md", text: "Fuel goes in.", score: 2.34567 },
    { file: "orbit.txt", text: "Orbit is high.", score: 1 },
  ],
  mode: "stub",
};

const input = () => screen.getByLabelText<HTMLInputElement>("Question");
const button = () => screen.getByRole<HTMLButtonElement>("button", { name: "Ask" });

function type(value: string) {
  fireEvent.change(input(), { target: { value } });
}

function submit(value = "How do rockets fly?") {
  type(value);
  fireEvent.click(button());
}

function deferred() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((r) => (resolve = r));
  return { promise, resolve };
}

describe("page", () => {
  it("has the heading and the form", () => {
    render(<Page />);

    expect(screen.getByRole("heading", { name: "Ask the docs" })).toBeTruthy();
    expect(input()).toBeTruthy();
  });
});

describe("AskForm", () => {
  it("has a text input limited to 1000 characters", () => {
    render(<AskForm />);

    expect(input().type).toBe("text");
    expect(input().maxLength).toBe(1000);
  });

  it("disables the button while the trimmed input is empty", () => {
    render(<AskForm />);
    expect(button().disabled).toBe(true);

    type("   ");
    expect(button().disabled).toBe(true);

    type(" x ");
    expect(button().disabled).toBe(false);
  });

  it("submits from the keyboard through a form with a submit button", async () => {
    fetchMock.mockResolvedValue(Response.json(ANSWER));
    render(<AskForm />);
    type("fuel");

    // Browsers turn Enter in the input into a submit of this form.
    expect(button().type).toBe("submit");
    expect(input().form).toBe(button().form);
    fireEvent.submit(input().form!);

    expect(await screen.findByText("Rockets burn fuel.")).toBeTruthy();
  });

  it("does not send a blank question even if the form is submitted", () => {
    render(<AskForm />);
    type("   ");

    fireEvent.submit(input().form!);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts the trimmed question to /api/ask", () => {
    fetchMock.mockReturnValue(deferred().promise);
    render(<AskForm />);

    submit("  fuel?  ");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(JSON.parse(init?.body as string)).toEqual({ question: "fuel?" });
  });

  it("is disabled and shows Thinking… while loading", async () => {
    const pending = deferred();
    fetchMock.mockReturnValue(pending.promise);
    render(<AskForm />);
    expect(screen.queryByText("Thinking…")).toBeNull();

    submit();

    expect(screen.getByText("Thinking…")).toBeTruthy();
    expect(button().disabled).toBe(true);
    fireEvent.submit(input().form!);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    pending.resolve(Response.json(ANSWER));
    await waitFor(() => expect(button().disabled).toBe(false));
    expect(screen.queryByText("Thinking…")).toBeNull();
  });

  it("renders the answer and each source", async () => {
    fetchMock.mockResolvedValue(Response.json(ANSWER));
    render(<AskForm />);

    submit();

    expect(await screen.findByText("Rockets burn fuel.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    for (const part of ["rockets.md", "2.35", "Fuel goes in."]) {
      expect(items[0].textContent).toContain(part);
    }
    for (const part of ["orbit.txt", "1.00", "Orbit is high."]) {
      expect(items[1].textContent).toContain(part);
    }
    expect(screen.queryByText("No matching sources.")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says when there are no matching sources", async () => {
    fetchMock.mockResolvedValue(
      Response.json({ answer: "Nothing found.", sources: [], mode: "stub" }),
    );
    render(<AskForm />);

    submit();

    expect(await screen.findByText("No matching sources.")).toBeTruthy();
    expect(screen.getByText("Nothing found.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  it("renders returned text as plain text", async () => {
    const markup = '<img src="x"> **bold**';
    fetchMock.mockResolvedValue(
      Response.json({
        answer: markup,
        sources: [{ file: "<b>.md", text: "<script>1</script>", score: 1 }],
        mode: "stub",
      }),
    );
    const { container } = render(<AskForm />);

    submit();

    expect(await screen.findByText(markup)).toBeTruthy();
    expect(screen.getByText("<script>1</script>")).toBeTruthy();
    expect(screen.getByText("<b>.md")).toBeTruthy();
    expect(container.querySelector("img, script, b")).toBeNull();
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
    ["a list detail", Response.json({ detail: [{ msg: "x" }] }, { status: 422 })],
    ["no detail", Response.json({}, { status: 422 })],
    ["a null body", Response.json(null, { status: 422 })],
    ["a non-JSON body", new Response("nope", { status: 422 })],
  ])("shows the status for an error with %s", async (_name, response) => {
    fetchMock.mockResolvedValue(response);
    render(<AskForm />);

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Request failed (422)",
    );
  });

  it.each([
    ["non-JSON", new Response("<html>", { status: 200 })],
    ["the wrong shape", Response.json({ hello: 1 })],
  ])("treats a success reply that is %s as a failure", async (_name, response) => {
    fetchMock.mockResolvedValue(response);
    render(<AskForm />);

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Request failed (200)",
    );
  });

  it("shows Request failed on a network failure", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    render(<AskForm />);

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Request failed",
    );
    expect(button().disabled).toBe(false);
  });

  it("clears the previous answer on a new submission", async () => {
    fetchMock.mockResolvedValueOnce(Response.json(ANSWER));
    render(<AskForm />);
    submit();
    await screen.findByText("Rockets burn fuel.");
    fetchMock.mockReturnValue(deferred().promise);

    submit("another");

    expect(screen.queryByText("Rockets burn fuel.")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Sources" })).toBeNull();
  });

  it("clears the previous error on a new submission", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    render(<AskForm />);
    submit();
    await screen.findByRole("alert");
    fetchMock.mockReturnValue(deferred().promise);

    submit("another");

    expect(screen.queryByRole("alert")).toBeNull();
  });
});
