// JSONL tracing shared with the agent service (docs/architecture.md#tracing).
// One line per action to stdout, and to logs/trace-<run_id>.jsonl when a run id is known.
import fs from "node:fs";
import path from "node:path";

const LOGS_DIR = process.env.LOGS_DIR ?? "logs";

export interface TraceEvent {
  run_id?: string;
  agent: string;
  action: string;
  input_summary?: string;
  output_summary?: string;
  memory_ids?: string[];
  gate?: unknown;
  status: "success" | "error" | "rejected";
  duration_ms?: number;
  [key: string]: unknown;
}

export function trace(event: TraceEvent): void {
  const line = JSON.stringify({ timestamp: new Date().toISOString(), service: "cognition", ...event });
  console.log(line);
  if (!event.run_id || !/^[A-Za-z0-9._-]+$/.test(event.run_id)) return;
  try {
    fs.mkdirSync(LOGS_DIR, { recursive: true });
    fs.appendFileSync(path.join(LOGS_DIR, `trace-${event.run_id}.jsonl`), line + "\n");
  } catch (err) {
    console.error(JSON.stringify({ service: "cognition", event: "trace_write_failed", error: String(err) }));
  }
}
