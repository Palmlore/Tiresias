// Tiresias cognition service. Only /health is implemented so far; the H-MEM engine
// (Meterless reference, Apache-2.0) is mounted next (docs/STATUS.md).
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";

const port = Number(process.env.COGNITION_PORT ?? 8100);

function send(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

export function handle(req: IncomingMessage, res: ServerResponse): void {
  if (req.method === "GET" && req.url === "/health") {
    send(res, 200, { service: "cognition", version: "0.1.0", engine: "h-mem (not mounted yet)" });
    return;
  }
  send(res, 501, {
    status: "error",
    error: { type: "NotImplemented", message: "See docs/STATUS.md", recoverable: false },
  });
}

if (import.meta.main ?? process.argv[1]?.endsWith("server.ts")) {
  createServer(handle).listen(port, "0.0.0.0", () => {
    console.log(JSON.stringify({ service: "cognition", event: "listening", port }));
  });
}
