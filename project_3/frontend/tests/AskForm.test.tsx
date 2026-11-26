// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Page from "../app/page";
import AskForm from "../components/AskForm";

const fetchMock = vi.fn<typeof fetch>();

const input = () => screen.getByLabelText("Question") as HTMLInputElement;
const button = () => screen.getByRole("button", { name: "Ask" }) as HTMLButtonElement;

function type(value: string) {
  fireEvent.change(input(), { target: { value } });
}

function submit(value: string) {
  type(value);
  fireEvent.click(button());
}

const success = {
  answer: "[stub] Based on a.md: Alpha.",
  sources: [
    { file: "a.md", text: "Alpha.", score: 1.23456 },
    { file: "b.txt", text: "<b>Beta</b>", score: 0.5 },
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

describe("page", () => {
  it("has the heading and the form", () => {
    render(<Page />);

    expect(screen.getByRole("heading", { name: "Ask the docs" })).toBeTruthy();
    expect(input().maxLength).toBe(1000);
    expect(button().type).toBe("submit");
  });
});

describe("AskForm", () => {
  it("disables the button while the trimmed input is empty", () => {
    render(<AskForm />);

    expect(button().disabled).toBe(true);
    type("   ");
    expect(button().disabled).toBe(true);
    type("hi");
    expect(button().disabled).toBe(false);
  });

  it("does not send a blank question when the form is submitted", () => {
    render(<AskForm />);
    type("   ");

    fireEvent.submit(input().closest("form")!);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts the question on form submission, as pressing Enter does", async () => {
    fetchMock.mockResolvedValue(Response.json(success));
    render(<AskForm />);
    type("  what is alpha?  ");

    fireEvent.submit(input().closest("form")!);
    await screen.findByText(success.answer);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("content-type")).toBe("application/json");
    expect(JSON.parse(init?.body as string)).toEqual({ question: "what is alpha?" });
  });

  it("shows Thinking… and blocks resubmission while loading", async () => {
    let resolve!: (response: Response) => void;
    fetchMock.mockReturnValue(new Promise<Response>((r) => (resolve = r)));
    render(<AskForm />);

    submit("hi");

    expect(screen.getByText("Thinking…")).toBeTruthy();
    expect(button().disabled).toBe(true);
    fireEvent.submit(input().closest("form")!);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    resolve(Response.json(success));
    await screen.findByText(success.answer);
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(button().disabled).toBe(false);
  });

  it("renders the answer and each source as plain text", async () => {
    fetchMock.mockResolvedValue(Response.json(success));
    const { container } = render(<AskForm />);

    submit("hi");

    await screen.findByText(success.answer);
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    const items = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(items).toHaveLength(2);
    expect(items[0]).toContain("a.md");
    expect(items[0]).toContain("1.23");
    expect(items[0]).not.toContain("1.234");
    expect(items[0]).toContain("Alpha.");
    expect(items[1]).toContain("b.txt");
    expect(items[1]).toContain("0.50");
    expect(items[1]).toContain("<b>Beta</b>");
    expect(container.querySelector("b")).toBeNull();
    expect(screen.queryByText("No matching sources.")).toBeNull();
  });

  it("says when there are no sources", async () => {
    fetchMock.mockResolvedValue(
      Response.json({ answer: "Nothing found.", sources: [], mode: "stub" }),
    );
    render(<AskForm />);

    submit("hi");

    await screen.findByText("Nothing found.");
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    expect(screen.getByText("No matching sources.")).toBeTruthy();
  });

  it("shows a string error detail", async () => {
    fetchMock.mockResolvedValue(
      Response.json({ detail: "Backend unavailable" }, { status: 502 }),
    );
    render(<AskForm />);

    submit("hi");

    expect((await screen.findByRole("alert")).textContent).toBe("Backend unavailable");
    expect(button().disabled).toBe(false);
  });

  it.each([
    ["a non-string detail", () => Response.json({ detail: [{ msg: "bad" }] }, { status: 422 }), 422],
    ["no detail", () => Response.json({}, { status: 500 }), 500],
    ["a null body", () => Response.json(null, { status: 500 }), 500],
    ["a non-JSON body", () => new Response("oops", { status: 503 }), 503],
  ])("shows the status for %s", async (_name, makeResponse, status) => {
    fetchMock.mockResolvedValue(makeResponse());
    render(<AskForm />);

    submit("hi");

    expect((await screen.findByRole("alert")).textContent).toBe(`Request failed (${status})`);
  });

  it("shows Request failed on a network failure", async () => {
    fetchMock.mockRejectedValue(new TypeError("offline"));
    render(<AskForm />);

    submit("hi");

    expect((await screen.findByRole("alert")).textContent).toBe("Request failed");
    expect(button().disabled).toBe(false);
  });

  it("clears the previous answer and error on a new submission", async () => {
    fetchMock
      .mockResolvedValueOnce(Response.json(success))
      .mockResolvedValueOnce(Response.json({ detail: "Broken" }, { status: 502 }))
      .mockReturnValueOnce(new Promise<Response>(() => {}));
    render(<AskForm />);

    submit("one");
    await screen.findByText(success.answer);

    submit("two");
    expect(screen.queryByText(success.answer)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Sources" })).toBeNull();
    await screen.findByRole("alert");

    submit("three");
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
