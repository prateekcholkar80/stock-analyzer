import assert from 'node:assert/strict';
import {
  chmod,
  link,
  mkdtemp,
  mkdir,
  realpath,
  rm,
  symlink,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import {
  loadSessionBoundary,
  MAX_SESSION_BYTES,
  PROVIDER_CONTRACT_ENV,
  SESSION_FILE_ENV,
  SessionConfigurationError,
} from '../src/session-boundary.js';


const HASH_NAME = `${'a'.repeat(64)}.json`;
const CONTRACT_VERSION = 'tijori.local_contract.v1';

async function fixture(context) {
  const createdRoot = await mkdtemp(join(tmpdir(), 'jarvis-tijori-session-'));
  const root = await realpath(createdRoot);
  await chmod(root, 0o700);
  const sessionFile = join(root, HASH_NAME);
  await writeFile(sessionFile, '{}', { mode: 0o600 });
  context.after(async () => rm(root, { recursive: true, force: true }));
  return {
    root,
    sessionFile,
    environment: {
      [SESSION_FILE_ENV]: sessionFile,
      [PROVIDER_CONTRACT_ENV]: CONTRACT_VERSION,
    },
  };
}

test('accepts an owner-only non-identifying session file', async (context) => {
  const { environment, sessionFile } = await fixture(context);

  const result = await loadSessionBoundary(environment);

  assert.deepEqual(result, {
    authenticated: true,
    providerContractVersion: CONTRACT_VERSION,
    sessionFile,
  });
  assert.equal(Object.isFrozen(result), true);
});

test('requires both bounded environment values and an absolute hashed filename', async (context) => {
  const { environment, sessionFile } = await fixture(context);
  const invalidEnvironments = [
    {},
    { ...environment, [SESSION_FILE_ENV]: 'relative/session.json' },
    { ...environment, [SESSION_FILE_ENV]: join(sessionFile, '..', 'account@example.com.json') },
    { ...environment, [PROVIDER_CONTRACT_ENV]: '' },
    { ...environment, [PROVIDER_CONTRACT_ENV]: 'x'.repeat(81) },
    { ...environment, [PROVIDER_CONTRACT_ENV]: 'bad contract version' },
  ];

  for (const candidate of invalidEnvironments) {
    await assert.rejects(
      () => loadSessionBoundary(candidate),
      SessionConfigurationError,
    );
  }
});

test('rejects missing, empty, oversized, and multiply-linked session files', async (context) => {
  const { environment, root, sessionFile } = await fixture(context);

  await rm(sessionFile);
  await assert.rejects(() => loadSessionBoundary(environment), SessionConfigurationError);

  await writeFile(sessionFile, '', { mode: 0o600 });
  await assert.rejects(() => loadSessionBoundary(environment), SessionConfigurationError);

  await writeFile(sessionFile, Buffer.alloc(MAX_SESSION_BYTES + 1), { mode: 0o600 });
  await assert.rejects(() => loadSessionBoundary(environment), SessionConfigurationError);

  await writeFile(sessionFile, '{}', { mode: 0o600 });
  await link(sessionFile, join(root, 'second-link.json'));
  await assert.rejects(() => loadSessionBoundary(environment), SessionConfigurationError);
});

test('rejects group-readable session files and directories', async (context) => {
  const { environment, root, sessionFile } = await fixture(context);

  await chmod(sessionFile, 0o640);
  await assert.rejects(() => loadSessionBoundary(environment), SessionConfigurationError);

  await chmod(sessionFile, 0o600);
  await chmod(root, 0o750);
  await assert.rejects(() => loadSessionBoundary(environment), SessionConfigurationError);
});

test('rejects a symbolic-link session file', async (context) => {
  const { environment, root, sessionFile } = await fixture(context);
  const target = join(root, 'target.json');
  await writeFile(target, '{}', { mode: 0o600 });
  await rm(sessionFile);
  await symlink(target, sessionFile);

  await assert.rejects(() => loadSessionBoundary(environment), SessionConfigurationError);
});

test('rejects session roots reached through a symbolic-link directory', async (context) => {
  const { root, sessionFile } = await fixture(context);
  const parent = await mkdtemp(join(tmpdir(), 'jarvis-tijori-parent-'));
  await chmod(parent, 0o700);
  const linkedRoot = join(parent, 'linked-root');
  await symlink(root, linkedRoot);
  context.after(async () => rm(parent, { recursive: true, force: true }));

  await assert.rejects(
    () => loadSessionBoundary({
      [SESSION_FILE_ENV]: join(linkedRoot, sessionFile.split('/').at(-1)),
      [PROVIDER_CONTRACT_ENV]: CONTRACT_VERSION,
    }),
    SessionConfigurationError,
  );
});

test('returns a generic error that does not disclose the rejected path', async () => {
  const sensitivePath = '/tmp/account@example.com-session.json';
  await assert.rejects(
    () => loadSessionBoundary({
      [SESSION_FILE_ENV]: sensitivePath,
      [PROVIDER_CONTRACT_ENV]: CONTRACT_VERSION,
    }),
    (error) => (
      error instanceof SessionConfigurationError
      && !error.message.includes(sensitivePath)
      && !error.message.includes('account@example.com')
    ),
  );
});

test('does not read or parse session-file contents', async (context) => {
  const { environment, sessionFile } = await fixture(context);
  await writeFile(sessionFile, 'not-json-and-never-returned', { mode: 0o600 });

  const result = await loadSessionBoundary(environment);

  assert.equal(Object.hasOwn(result, 'cookies'), false);
  assert.equal(Object.hasOwn(result, 'storageState'), false);
});

test('rejects a nested directory with permissive ownership boundary', async (context) => {
  const { root } = await fixture(context);
  const nested = join(root, 'nested');
  await mkdir(nested, { mode: 0o755 });
  const nestedFile = join(nested, HASH_NAME);
  await writeFile(nestedFile, '{}', { mode: 0o600 });

  await assert.rejects(
    () => loadSessionBoundary({
      [SESSION_FILE_ENV]: nestedFile,
      [PROVIDER_CONTRACT_ENV]: CONTRACT_VERSION,
    }),
    SessionConfigurationError,
  );
});
