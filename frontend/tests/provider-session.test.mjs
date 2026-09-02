import assert from "node:assert/strict";
import test from "node:test";

import {
  ProviderSessionClientError,
  createProviderSessionClient,
} from "../lib/provider-session.ts";

const target = {
  provider_connection_id: "provider.tijori.local",
  provider: "tijori",
  account_reference_hash: "a".repeat(64),
};

const lifecycle = {
  schema_version: "jarvis.http_provider_session.v1",
  provider_connection_id: target.provider_connection_id,
  provider: target.provider,
  status: "ready",
  checked_at: "2026-08-29T12:00:00+05:30",
  provisioned_at: "2026-08-29T11:59:00+05:30",
  expires_at: "2026-08-29T23:59:00+05:30",
  revoked_at: null,
  revocation_reason: null,
  lifecycle_fingerprint: "b".repeat(64),
};

function recordingClient(response = new Response(JSON.stringify(lifecycle))) {
  const calls = [];
  const client = createProviderSessionClient({
    apiBase: "http://127.0.0.1:8000/",
    sessionId: "browser/session",
    accessToken: "browser-capability-token",
    fetcher: async (input, init) => {
      calls.push({ input, init });
      return response;
    },
  });
  return { calls, client };
}

test("status uses the authenticated secret-free provider route", async () => {
  const { calls, client } = recordingClient();

  const result = await client.status(target);

  assert.equal(result.status, "ready");
  assert.equal(
    calls[0].input,
    "http://127.0.0.1:8000/api/v1/sessions/browser%2Fsession/provider-session/status",
  );
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.headers["X-Jarvis-Session-Token"], "browser-capability-token");
  assert.deepEqual(JSON.parse(calls[0].init.body), { target });
  assert.equal(calls[0].init.body.includes("password"), false);
  assert.equal(calls[0].init.body.includes("cookie"), false);
});

test("provision always carries explicit interaction authorization", async () => {
  const { calls, client } = recordingClient();

  await client.provision(target, { idempotencyKey: "connect.1" });

  assert.deepEqual(JSON.parse(calls[0].init.body), {
    idempotency_key: "connect.1",
    target,
    user_interaction_authorized: true,
    replace_existing: false,
  });
});

test("revoke always requires secure deletion", async () => {
  const { calls, client } = recordingClient();

  await client.revoke(target, { idempotencyKey: "revoke.1" });

  assert.deepEqual(JSON.parse(calls[0].init.body), {
    idempotency_key: "revoke.1",
    target,
    reason: "user_requested",
    secure_delete_required: true,
  });
});

test("HTTP failures are sanitized without reflecting response content", async () => {
  const response = new Response("provider leaked secret", { status: 503 });
  const { client } = recordingClient(response);

  await assert.rejects(
    client.status(target),
    (error) => error instanceof ProviderSessionClientError
      && error.status === 503
      && error.message === "The provider connection service is unavailable."
      && !error.message.includes("secret"),
  );
});

test("malformed success payloads fail closed", async () => {
  const response = new Response(JSON.stringify({ ...lifecycle, status: "mystery" }));
  const { client } = recordingClient(response);

  await assert.rejects(
    client.status(target),
    (error) => error instanceof ProviderSessionClientError && error.status === 502,
  );
});

test("network failures have a distinct sanitized error", async () => {
  const client = createProviderSessionClient({
    apiBase: "http://127.0.0.1:8000",
    sessionId: "session-1",
    accessToken: "token",
    fetcher: async () => { throw new Error("socket contained sensitive detail"); },
  });

  await assert.rejects(
    client.status(target),
    (error) => error instanceof ProviderSessionClientError
      && error.status === 0
      && !error.message.includes("sensitive"),
  );
});
