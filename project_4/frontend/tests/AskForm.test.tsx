// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import AskForm from "../components/AskForm";
import type { AskResponse } from "../lib/types";

const fetchMock = vi.fn<typeof fetch>();

const answer: AskResponse = {
  answer: "Lava is molten rock.",
  sources: [
    { file: "volcano.md", text: "Molten rock at the surface.", score: 2.3456 },
    { file: "geology.txt", text: "Rocks come in three kinds.", score: 1 },
  ],
  mode: "stub",
};

function input(): HTMLInputElement {
  return screen.getByLabelText("Question");
}

function button(): HTMLButtonElement {
  return screen.getByRole("button", { name: "Ask" });
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

/** A fetch that stays pending until the returned function is called. */
function holdReply() {
  let release!: (response: Response) => void;
  fetchMock.mockImplementationOnce(
    () => new Promise<Response>((resolve) => (release = resolve)),
  );
  return (body: unknown, status = 200) =>
    release(Response.json(body, { status }));
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
  test("has a labelled text input limited to 1000 characters", () => {
    expect(input().type).toBe("text");
    expect(input().maxLength).toBe(1000);
  });

  test("disables the button while the trimmed input is empty", () => {
    expect(button().disabled).toBe(true);
    type("   ");
    expect(button().disabled).toBe(true);
    type(" lava ");
    expect(button().disabled).toBe(false);
  });

  test("does not submit an empty question", () => {
    type("   ");
    fireEvent.submit(input().closest("form")!);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("posts the trimmed question to /api/ask", async () => {
    reply(answer);

    submit("  What is lava?  ");
    await screen.findByText(answer.answer);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/ask");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({
      question: "What is lava?",
    });
  });

  test("submits with a native form so that Enter works", () => {
    // jsdom does not turn Enter into a submit, so check the native wiring.
    expect(input().form).not.toBeNull();
    expect(button().type).toBe("submit");
    expect(button().form).toBe(input().form);

    holdReply();
    type("lava");
    fireEvent.submit(input().form!);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  test("disables the button and shows Thinking… while waiting", async () => {
    const release = holdReply();
    expect(screen.queryByText("Thinking…")).toBeNull();

    submit();

    expect(button().disabled).toBe(true);
    expect(screen.getByText("Thinking…")).toBeTruthy();
    fireEvent.submit(input().form!);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    release(answer);
    await waitFor(() => expect(button().disabled).toBe(false));
    expect(screen.queryByText("Thinking…")).toBeNull();
  });

  test("renders the answer and each source with a two-decimal score", async () => {
    reply(answer);

    submit();

    expect(await screen.findByText(answer.answer)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    for (const part of ["volcano.md", "2.35", "Molten rock at the surface."]) {
      expect(items[0].textContent).toContain(part);
    }
    for (const part of ["geology.txt", "1.00", "Rocks come in three kinds."]) {
      expect(items[1].textContent).toContain(part);
    }
    expect(screen.queryByText("No matching sources.")).toBeNull();
  });

  test("shows a message when there are no sources", async () => {
    reply({ answer: "Nothing found.", sources: [], mode: "stub" });

    submit();

    expect(await screen.findByText("No matching sources.")).toBeTruthy();
    expect(screen.getByText("Nothing found.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeTruthy();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  test("renders returned text as plain text", async () => {
    const markup = '<img src="x" alt="injected">';
    reply({
      answer: markup,
      sources: [{ file: "<b>f</b>.md", text: "**bold** <i>x</i>", score: 1 }],
      mode: "proxy",
    });

    submit();

    expect(await screen.findByText(markup)).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
    expect(document.querySelector("b, i")).toBeNull();
    expect(screen.getByText("**bold** <i>x</i>")).toBeTruthy();
  });

  test("shows a string error detail", async () => {
    reply({ detail: "LLM request failed" }, 502);

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "LLM request failed",
    );
    expect(button().disabled).toBe(false);
  });

  test.each([
    ["a list detail", { detail: [{ msg: "bad" }] }],
    ["no detail", {}],
    ["a null body", null],
  ])("shows the status when there is %s", async (_name, body) => {
    reply(body, 422);

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Request failed (422)",
    );
  });

  test.each([500, 200])(
    "shows the status when a %i reply is not JSON",
    async (status) => {
      fetchMock.mockImplementationOnce(
        async () => new Response("oops", { status }),
      );

      submit();

      expect((await screen.findByRole("alert")).textContent).toBe(
        `Request failed (${status})`,
      );
    },
  );

  test("shows Request failed on a network failure", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Request failed",
    );
    expect(button().disabled).toBe(false);
  });

  test("clears the previous answer on a new submission", async () => {
    reply(answer);
    submit();
    await screen.findByText(answer.answer);

    holdReply();
    submit("another");

    expect(screen.queryByText(answer.answer)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Sources" })).toBeNull();
  });

  test("clears the previous error on a new submission", async () => {
    reply({ detail: "LLM request failed" }, 502);
    submit();
    await screen.findByRole("alert");

    holdReply();
    submit("another");

    expect(screen.queryByRole("alert")).toBeNull();
  });
});
