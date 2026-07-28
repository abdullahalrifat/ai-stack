import assert from "node:assert/strict";
import test from "node:test";

import {
  answerAfterEvent,
  diffAfterEvent,
  sourceUrls,
} from "../src/runEvents.ts";

test("audited completion replaces any provisional output", () => {
  const answer = answerAfterEvent("provisional", {
    event_type: "run_completed",
    payload: { answer: "audited final" },
  });

  assert.equal(answer, "audited final");
});

test("output batches append and unrelated events preserve state", () => {
  const appended = answerAfterEvent("abc", {
    event_type: "output_delta",
    payload: { content: "def" },
  });
  const preserved = diffAfterEvent("existing", {
    event_type: "planning",
    payload: {},
  });

  assert.equal(appended, "abcdef");
  assert.equal(preserved, "existing");
});

test("source URLs are unique and punctuation is removed", () => {
  assert.deepEqual(
    sourceUrls(
      "See https://example.com/report.pdf, then https://example.com/report.pdf.",
    ),
    ["https://example.com/report.pdf"],
  );
});
