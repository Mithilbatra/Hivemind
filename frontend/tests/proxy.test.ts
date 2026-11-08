import assert from "node:assert/strict";
import { test } from "node:test";
import { NextRequest } from "next/server";
import { proxy } from "../proxy";

const at = (host: string) => proxy(new NextRequest("http://localhost/api/tasks", { headers: { host } }));

test("only local hosts get through (a guard against DNS rebinding)", () => {
  for (const host of ["localhost:47213", "127.0.0.1:3000", "[::1]:3000", "localhost"]) {
    assert.equal(at(host).headers.get("x-middleware-next"), "1", host);
  }
  for (const host of ["evil.example", "localhost.evil.example:47213", "127.0.0.1.nip.io", ""]) {
    assert.equal(at(host).status, 421, host);
  }
});

test("no page can be framed (another site must not overlay the Approve button)", async () => {
  const config = require("../../next.config.js");
  const [rule] = await config.headers();
  const headers = Object.fromEntries(rule.headers.map((h: { key: string; value: string }) => [h.key, h.value]));
  assert.equal(rule.source, "/:path*");
  assert.deepEqual(headers, { "X-Frame-Options": "DENY", "Content-Security-Policy": "frame-ancestors 'none'" });
});
