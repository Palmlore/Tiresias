// Tiresias cognition service: REST over the Meterless H-MEM engine (contracts/openapi/cognition.yaml).
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { Memory, NotFound, type Gate } from "./engine.ts";
import { trace } from "./trace.ts";

const VERSION = "0.2.0";
const ENGINE = { name: "H-MEM", source: "Meterless reference engine @1742dec (unmodified)" };
const FEEDBACK_KINDS = new Set(["helpful", "not_helpful", "wrong"]);
const MAX_BODY = 64 * 1024;

class BadRequest extends Error {}

function send(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

async function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  let size = 0;
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > MAX_BODY) throw new BadRequest("body too large");
    chunks.push(chunk as Buffer);
  }
  if (chunks.length === 0) return {};
  try {
    const parsed = JSON.parse(Buffer.concat(chunks).toString("utf-8"));
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
    return parsed as Record<string, unknown>;
  } catch {
    throw new BadRequest("body must be a JSON object");
  }
}

function str(body: Record<string, unknown>, key: string, required = false): string | undefined {
  const value = body[key];
  if (value === undefined || value === null) {
    if (required) throw new BadRequest(`${key} is required`);
    return undefined;
  }
  if (typeof value !== "string" || (required && value.trim() === "")) {
    throw new BadRequest(`${key} must be a non-empty string`);
  }
  return value;
}

function gateFrom(value: unknown): Gate | undefined {
  if (value === undefined) return undefined;
  const g = value as Partial<Gate>;
  if (!["auto", "interactive", "scripted"].includes(g.mode as string)) throw new BadRequest("gate.mode is invalid");
  if (!["approve", "reject"].includes(g.decision as string)) throw new BadRequest("gate.decision is invalid");
  return { mode: g.mode!, decision: g.decision!, decidedBy: String(g.decidedBy ?? "unknown"), reason: g.reason };
}

export function createHandler(memory: Memory) {
  return async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const started = Date.now();
    const url = new URL(req.url ?? "/", "http://localhost");
    const runId = (req.headers["x-run-id"] as string | undefined) ?? undefined;
    const route = `${req.method} ${url.pathname}`;
    try {
      if (route === "GET /health") {
        return send(res, 200, { service: "cognition", version: VERSION, engine: ENGINE, lessons: memory.count() });
      }
      if (route === "GET /memory") {
        return send(res, 200, { lessons: memory.list(url.searchParams.get("namespace") ?? undefined) });
      }
      if (route === "GET /ledger") {
        return send(res, 200, { entries: memory.ledger(url.searchParams.get("memory_id") ?? undefined) });
      }
      const body = req.method === "POST" ? await readJson(req) : {};
      if (route === "POST /memory/remember") {
        const out = memory.remember({
          content: str(body, "content", true)!,
          namespace: str(body, "namespace"),
          runId: runId ?? str(body, "run_id"),
          agentId: str(body, "agent_id"),
          tags: Array.isArray(body.tags) ? body.tags.map(String) : undefined,
          confidence: typeof body.confidence === "number" ? body.confidence : undefined,
          gate: gateFrom(body.gate),
        });
        trace({
          run_id: runId, agent: "Memory", action: "remember",
          input_summary: String(body.content).slice(0, 160),
          output_summary: out.stored ? `stored ${out.lesson!.id}` : "rejected at memory gate",
          memory_ids: out.lesson ? [out.lesson.id] : [], gate: out.gate,
          status: out.stored ? "success" : "rejected", duration_ms: Date.now() - started,
        });
        return send(res, 200, out);
      }
      if (route === "POST /memory/recall") {
        const query = str(body, "query", true)!;
        const out = memory.recall(query, {
          namespace: str(body, "namespace"),
          topN: typeof body.top_n === "number" ? body.top_n : undefined,
          threshold: typeof body.threshold === "number" ? body.threshold : undefined,
        });
        trace({
          run_id: runId, agent: "Memory", action: "recall", input_summary: query.slice(0, 160),
          output_summary: `${out.lessons.length} lesson(s); strategy ${out.trace.strategy}`,
          memory_ids: out.lessons.map((l) => l.id), status: "success", duration_ms: Date.now() - started,
        });
        return send(res, 200, out);
      }
      if (route === "POST /memory/feedback") {
        const memoryId = str(body, "memory_id", true)!;
        const kind = str(body, "kind", true)!;
        if (!FEEDBACK_KINDS.has(kind)) throw new BadRequest("kind must be helpful, not_helpful or wrong");
        const lesson = memory.feedback(memoryId, kind as "helpful" | "not_helpful" | "wrong");
        trace({
          run_id: runId, agent: "Memory", action: "feedback", input_summary: `${memoryId}: ${kind}`,
          output_summary: `confidence ${lesson.confidence}`, memory_ids: [memoryId], status: "success",
          duration_ms: Date.now() - started,
        });
        return send(res, 200, { lesson });
      }
      if (route === "POST /memory/purge") {
        const namespace = url.searchParams.get("namespace") ?? str(body, "namespace");
        const out = memory.purge({ namespace, runId, requestedBy: str(body, "requested_by") });
        trace({
          run_id: runId, agent: "Memory", action: "purge", input_summary: `namespace ${namespace ?? "*"}`,
          output_summary: `purged ${out.purged}`, status: "success", duration_ms: Date.now() - started,
        });
        return send(res, 200, out);
      }
      return send(res, 404, { status: "error", error: { type: "NotFound", message: route, recoverable: false } });
    } catch (err) {
      const [status, type] =
        err instanceof BadRequest ? [400, "BadRequest"] : err instanceof NotFound ? [404, "NotFound"] : [500, "Internal"];
      trace({ run_id: runId, agent: "Memory", action: route, status: "error", output_summary: String(err) });
      return send(res, status, {
        status: "error",
        error: { type, message: err instanceof Error ? err.message : String(err), recoverable: status < 500 },
      });
    }
  };
}

if (import.meta.main ?? process.argv[1]?.endsWith("server.ts")) {
  const port = Number(process.env.COGNITION_PORT ?? 8100);
  const memory = new Memory({ persistDir: process.env.MEMORY_DIR ?? ".memory" });
  createServer(createHandler(memory)).listen(port, "0.0.0.0", () => {
    console.log(JSON.stringify({ service: "cognition", event: "listening", port, engine: ENGINE, lessons: memory.count() }));
  });
}
