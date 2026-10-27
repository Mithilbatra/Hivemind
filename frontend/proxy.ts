import { NextRequest, NextResponse } from "next/server";

// Hivemind only listens on 127.0.0.1. A page that rebinds its own domain to that address still sends its own
// Host header, so refuse anything else before the /api rewrite can forward it (the API checks Host too).
const LOCAL = new Set(["localhost", "127.0.0.1", "[::1]"]);

export function proxy(request: NextRequest) {
  const host = (request.headers.get("host") ?? "").replace(/:\d+$/, "");
  return LOCAL.has(host) ? NextResponse.next() : new NextResponse("Unknown host", { status: 421 });
}
