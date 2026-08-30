import assert from 'node:assert/strict';
import test from 'node:test';

import {
  PROVISION_TIMEOUT_ENV,
  ProvisionSessionCliError,
  SESSION_FILE_ENV,
  assertProvisioningNodeVersion,
  runInteractiveSessionCli,
} from '../src/provision-session-cli.js';


const SESSION_FILE = `/private/tmp/${'a'.repeat(64)}.json`;
const SESSION_HASH = 'b'.repeat(64);

function outputSink() {
  const writes = [];
  return {
    output: { write(value) { writes.push(value); } },
    writes,
  };
}

test('passes only the scoped target and bounded timeout to provisioning', async () => {
  const sink = outputSink();
  const calls = [];
  const result = await runInteractiveSessionCli(
    {
      [SESSION_FILE_ENV]: SESSION_FILE,
      [PROVISION_TIMEOUT_ENV]: '30000',
      TIJORI_USERNAME: 'must-not-pass',
      TIJORI_PASSWORD: 'must-not-pass',
      COOKIE: 'must-not-pass',
    },
    {
      async provisioner(options) {
        calls.push(options);
        return { status: 'ready', session_reference_hash: SESSION_HASH };
      },
      output: sink.output,
    },
  );

  assert.deepEqual(calls, [{ sessionFile: SESSION_FILE, timeoutMs: 30_000 }]);
  assert.deepEqual(result, {
    status: 'ready',
    session_reference_hash: SESSION_HASH,
  });
  assert.equal(Object.isFrozen(result), true);
  assert.deepEqual(sink.writes, [`${JSON.stringify(result)}\n`]);
  assert.equal(sink.writes[0].includes('must-not-pass'), false);
  assert.equal(sink.writes[0].includes(SESSION_FILE), false);
});

test('uses the bounded default timeout without reading unrelated environment', async () => {
  const sink = outputSink();
  let received;
  await runInteractiveSessionCli(
    { [SESSION_FILE_ENV]: SESSION_FILE, API_KEY: 'ignored-secret' },
    {
      async provisioner(options) {
        received = options;
        return { status: 'ready', session_reference_hash: SESSION_HASH };
      },
      output: sink.output,
    },
  );

  assert.equal(received.timeoutMs, 300_000);
  assert.equal(JSON.stringify(received).includes('ignored-secret'), false);
});

test('rejects missing unsafe and malformed session targets before provisioning', async () => {
  for (const sessionFile of [
    undefined,
    'relative.json',
    '/private/tmp/session.json',
    `/private/tmp/${'A'.repeat(64)}.json`,
  ]) {
    let calls = 0;
    await assert.rejects(
      () => runInteractiveSessionCli(
        { [SESSION_FILE_ENV]: sessionFile },
        {
          async provisioner() { calls += 1; },
          output: outputSink().output,
        },
      ),
      ProvisionSessionCliError,
    );
    assert.equal(calls, 0);
  }
});

test('rejects timeout escape and command-line arguments before provisioning', async () => {
  for (const timeout of ['29999', '900001', '1.5', '-1', 'secret']) {
    await assert.rejects(
      () => runInteractiveSessionCli(
        { [SESSION_FILE_ENV]: SESSION_FILE, [PROVISION_TIMEOUT_ENV]: timeout },
        { output: outputSink().output },
      ),
      ProvisionSessionCliError,
    );
  }
  await assert.rejects(
    () => runInteractiveSessionCli(
      { [SESSION_FILE_ENV]: SESSION_FILE },
      { argumentsValue: ['--password=must-not-pass'], output: outputSink().output },
    ),
    ProvisionSessionCliError,
  );
});

test('rejects malformed provisioner results without releasing them', async () => {
  const secret = 'Bearer provider-secret';
  for (const value of [
    null,
    {},
    { status: 'failed', session_reference_hash: SESSION_HASH },
    { status: 'ready', session_reference_hash: 'bad' },
  ]) {
    const sink = outputSink();
    await assert.rejects(
      () => runInteractiveSessionCli(
        { [SESSION_FILE_ENV]: SESSION_FILE },
        { provisioner: async () => value, output: sink.output },
      ),
      ProvisionSessionCliError,
    );
    assert.deepEqual(sink.writes, []);
  }
  await assert.rejects(
    () => runInteractiveSessionCli(
      { [SESSION_FILE_ENV]: SESSION_FILE },
      {
        provisioner: async () => { throw new Error(secret); },
        output: outputSink().output,
      },
    ),
    (error) => (
      error instanceof ProvisionSessionCliError
      && !error.message.includes(secret)
    ),
  );
});

test('fails closed when canonical output cannot be written', async () => {
  await assert.rejects(
    () => runInteractiveSessionCli(
      { [SESSION_FILE_ENV]: SESSION_FILE },
      {
        provisioner: async () => ({
          status: 'ready', session_reference_hash: SESSION_HASH,
        }),
        output: { write() { throw new Error('output-secret'); } },
      },
    ),
    ProvisionSessionCliError,
  );
});

test('accepts only the pinned Node 24 runtime', () => {
  assert.doesNotThrow(() => assertProvisioningNodeVersion('24.12.0'));
  for (const version of ['22.18.0', '25.2.1', 'invalid']) {
    assert.throws(() => assertProvisioningNodeVersion(version), ProvisionSessionCliError);
  }
});
