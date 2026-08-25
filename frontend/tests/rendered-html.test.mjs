import assert from "node:assert/strict";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the Jarvis investment console", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>Jarvis · Investment Intelligence<\/title>/i);
  assert.match(html, /JARVIS/);
  assert.match(html, /Investment intelligence/i);
  assert.match(html, /Research division/i);
  assert.match(html, /Debate chamber/i);
  assert.match(html, /Operation matrix/i);
  assert.match(html, /Live agent activity/i);
  assert.match(html, /Agent telemetry will appear/i);
  assert.match(html, /Wake Jarvis/i);
  assert.match(html, /IST · (?:<!-- -->)?--:--/i);
  assert.match(html, /Research, not guaranteed investment advice/i);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton/i);
});
