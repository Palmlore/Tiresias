import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { handle } from "../src/server.ts";

test("health reports the service", async () => {
  const server = createServer(handle).listen(0);
  const { port } = server.address() as AddressInfo;
  const body = await (await fetch(`http://127.0.0.1:${port}/health`)).json();
  server.close();
  assert.equal(body.service, "cognition");
});
