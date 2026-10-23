import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { Memory } from "../src/engine.ts";
import { createHandler } from "../src/server.ts";

const LESSON =
  "Restated revenue between 10-K filings: check 8-K item 2.01 for a disposition, then reconcile against the spun-off entity's companyfacts revenue";

function tmp(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "tiresias-mem-"));
}

test("remember then recall a lesson in its namespace", () => {
  const m = new Memory({ persistDir: tmp() });
  const { stored, lesson } = m.remember({ content: LESSON, runId: "run-1", agentId: "Critic" });
  assert.equal(stored, true);
  const out = m.recall("why was GE revenue restated between filings after a spin-off?");
  assert.ok(out.lessons.some((l) => l.id === lesson!.id));
});

test("an unrelated question does not recall the lesson", () => {
  const m = new Memory({ persistDir: tmp() });
  m.remember({ content: LESSON });
  assert.equal(m.recall("why did taxi trips fall in Brooklyn").lessons.length, 0);
});

test("namespaces are isolated", () => {
  const m = new Memory({ persistDir: tmp() });
  m.remember({ content: LESSON, namespace: "lessons" });
  assert.equal(m.recall("restated revenue spin-off", { namespace: "asker:auditor" }).lessons.length, 0);
});

test("memory gate reject is ledgered but never stored", () => {
  const m = new Memory({ persistDir: tmp() });
  const out = m.remember({
    content: LESSON,
    gate: { mode: "scripted", decision: "reject", decidedBy: "input.decisions" },
  });
  assert.equal(out.stored, false);
  assert.equal(m.count(), 0);
  assert.ok(m.ledger().some((e) => (e.details as { event?: string })?.event === "memory_gate_reject"));
});

test("purge clears lessons, the ledger keeps the purge event, and state survives restart", () => {
  const dir = tmp();
  const m = new Memory({ persistDir: dir });
  m.remember({ content: LESSON, runId: "run-1" });
  assert.equal(new Memory({ persistDir: dir }).count(), 1, "persisted across restart");
  assert.deepEqual(m.purge({ runId: "run-2" }), { purged: 1 });
  const after = new Memory({ persistDir: dir });
  assert.equal(after.count(), 0);
  assert.ok(after.ledger().some((e) => (e.details as { event?: string })?.event === "purge"));
});

test("REST: health, remember, recall, purge, bad input", async () => {
  const server = createServer(createHandler(new Memory({ persistDir: tmp() }))).listen(0);
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const post = (p: string, body: unknown) =>
    fetch(base + p, { method: "POST", headers: { "x-run-id": "test-run" }, body: JSON.stringify(body) });
  try {
    assert.equal((await (await fetch(`${base}/health`)).json()).engine.name, "H-MEM");
    assert.equal((await (await post("/memory/remember", { content: LESSON })).json()).stored, true);
    const recall = await (await post("/memory/recall", { query: "restated revenue spin-off" })).json();
    assert.equal(recall.lessons.length, 1);
    assert.equal((await (await post("/memory/purge", {})).json()).purged, 1);
    assert.equal((await post("/memory/remember", {})).status, 400);
    const unknown = await post("/memory/feedback", { memory_id: "no-such-id", kind: "helpful" });
    assert.equal(unknown.status, 404, "unknown memory id is a 404, not a 500");
  } finally {
    server.close();
  }
});
