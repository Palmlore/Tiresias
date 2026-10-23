// Tiresias memory on top of the vendored Meterless H-MEM reference engine (unmodified).
// Adds what Tiresias needs around it: namespaces (mapped to H-MEM's chatId scope),
// a memory gate, and a purge that the append-only trust ledger records.
import { HMEM } from "../engine/hmem/reference/src/index.ts";
import type { FeedbackKind, LedgerEntry, MemoryRecord } from "../engine/hmem/reference/src/index.ts";

// Re-exported so the official conformance runner can be pointed at this module
// (`npm run conformance`), proving the engine the service loads is conformant.
export { HMEM };

export type GateMode = "auto" | "interactive" | "scripted";

export interface Gate {
  mode: GateMode;
  decision: "approve" | "reject";
  decidedBy: string;
  reason?: string;
}

export interface RememberInput {
  content: string;
  namespace?: string;
  runId?: string;
  agentId?: string;
  tags?: string[];
  confidence?: number;
  gate?: Gate;
}

export interface Lesson {
  id: string;
  content: string;
  namespace: string;
  confidence: number;
  tags: string[];
  runId?: string;
  relevance?: number;
}

export const DEFAULT_NAMESPACE = "lessons";
// The spec's 0.35 default assumes richer embeddings; with the reference's model-free embedder,
// related questions score 0.27-0.30 against a lesson and unrelated ones 0.19-0.20 (measured
// 2026-10-22), so Tiresias tunes the threshold per domain as the spec allows.
export const RECALL_THRESHOLD = Number(process.env.RECALL_THRESHOLD ?? 0.25);
const ACTOR = "cognition";

function toLesson(m: MemoryRecord, relevance?: number): Lesson {
  return {
    id: m.id,
    content: m.content,
    namespace: m.chatId ?? DEFAULT_NAMESPACE,
    confidence: m.confidence,
    tags: m.tags,
    runId: m.goalRunId,
    ...(relevance === undefined ? {} : { relevance: Number(relevance.toFixed(4)) }),
  };
}

export class Memory {
  readonly hmem: HMEM;

  constructor(opts: { persistDir?: string; clock?: () => number } = {}) {
    this.hmem = new HMEM({ persistDir: opts.persistDir, clock: opts.clock, actor: ACTOR });
  }

  // The memory gate: H-MEM's "programmable approval boundary". A rejected lesson is
  // logged to the ledger (as evidence of the decision) but never materialized.
  remember(input: RememberInput): { stored: boolean; lesson?: Lesson; gate: Gate } {
    const gate: Gate = input.gate ?? { mode: "auto", decision: "approve", decidedBy: "policy:default" };
    const namespace = input.namespace ?? DEFAULT_NAMESPACE;
    if (gate.decision === "reject") {
      this.hmem.ledger.log(`gate:${Date.now()}`, "dream_rejected", ACTOR, {
        details: { event: "memory_gate_reject", namespace, runId: input.runId, gate, content: input.content },
      });
      return { stored: false, gate };
    }
    const record = this.hmem.add({
      content: input.content,
      type: "general",
      layer: "long_term",
      tags: [...(input.tags ?? []), "lesson", `agent:${input.agentId ?? "unknown"}`],
      confidence: input.confidence ?? 0.7,
      source: `tiresias:${input.agentId ?? "unknown"}`,
      provenance: {
        origin: "plan_completion",
        learnedAt: this.hmem.clock(),
        label: `lesson from run ${input.runId ?? "n/a"} (gate ${gate.mode}: ${gate.decidedBy})`,
        refs: input.runId ? [input.runId] : [],
      },
      chatId: namespace,
      goalRunId: input.runId,
    });
    return { stored: true, lesson: toLesson(record), gate };
  }

  recall(query: string, opts: { namespace?: string; topN?: number; threshold?: number } = {}) {
    const namespace = opts.namespace ?? DEFAULT_NAMESPACE;
    const threshold = opts.threshold ?? RECALL_THRESHOLD;
    const result = this.hmem.query(query, { topN: opts.topN, threshold, chatId: namespace });
    return {
      lessons: result.memories
        .filter((r) => r.memory.chatId === namespace)
        .map((r) => toLesson(r.memory, r.relevance)),
      context: result.context,
      trace: result.trace,
    };
  }

  feedback(memoryId: string, kind: FeedbackKind): Lesson {
    // Check first: the engine throws a plain Error for an unknown id, which surfaced as a 500.
    if (!this.hmem.store.get(memoryId)) throw new NotFound(`memory ${memoryId}`);
    this.hmem.feedback(memoryId, kind);
    return toLesson(this.hmem.store.get(memoryId)!);
  }

  list(namespace?: string): Lesson[] {
    return this.hmem.store
      .all()
      .filter((m) => namespace === undefined || m.chatId === namespace)
      .map((m) => toLesson(m));
  }

  // Purge deletes the lessons (each deletion is ledgered by the engine) and appends one
  // summary entry, so the audit trail survives the purge.
  purge(opts: { namespace?: string; runId?: string; requestedBy?: string } = {}): { purged: number } {
    const targets = this.list(opts.namespace);
    for (const lesson of targets) this.hmem.store.delete(lesson.id, "purge");
    this.hmem.ledger.log(`purge:${this.hmem.clock()}`, "delete", ACTOR, {
      details: {
        event: "purge",
        namespace: opts.namespace ?? "*",
        count: targets.length,
        runId: opts.runId,
        requestedBy: opts.requestedBy ?? "api",
      },
    });
    return { purged: targets.length };
  }

  ledger(memoryId?: string): readonly LedgerEntry[] {
    return memoryId ? this.hmem.ledger.history(memoryId) : this.hmem.ledger.all();
  }

  count(): number {
    return this.hmem.store.all().length;
  }
}

export class NotFound extends Error {}
