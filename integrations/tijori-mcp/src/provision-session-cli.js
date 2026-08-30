import process from 'node:process';
import { isAbsolute } from 'node:path';
import { pathToFileURL } from 'node:url';

import { provisionInteractiveSession } from './interactive-session.js';


export const SESSION_FILE_ENV = 'JARVIS_TIJORI_SESSION_FILE';
export const PROVISION_TIMEOUT_ENV = 'JARVIS_TIJORI_PROVISION_TIMEOUT_MS';

const SESSION_FILE_PATTERN = /(?:^|\/)[a-f0-9]{64}\.json$/;
const DEFAULT_TIMEOUT_MS = 5 * 60_000;
const MIN_TIMEOUT_MS = 30_000;
const MAX_TIMEOUT_MS = 15 * 60_000;

export class ProvisionSessionCliError extends Error {
  constructor() {
    super('Tijori interactive session command could not complete safely');
    this.name = 'ProvisionSessionCliError';
  }
}

export async function runInteractiveSessionCli(
  environment = process.env,
  {
    provisioner = provisionInteractiveSession,
    output = process.stdout,
    argumentsValue = [],
  } = {},
) {
  if (
    environment === null
    || typeof environment !== 'object'
    || typeof provisioner !== 'function'
    || output === null
    || typeof output?.write !== 'function'
    || !Array.isArray(argumentsValue)
    || argumentsValue.length !== 0
  ) {
    throw new ProvisionSessionCliError();
  }
  const sessionFile = environment[SESSION_FILE_ENV];
  const timeoutMs = parseTimeout(environment[PROVISION_TIMEOUT_ENV]);
  if (
    typeof sessionFile !== 'string'
    || !isAbsolute(sessionFile)
    || !SESSION_FILE_PATTERN.test(sessionFile)
  ) {
    throw new ProvisionSessionCliError();
  }

  let result;
  try {
    result = await provisioner({ sessionFile, timeoutMs });
  } catch {
    throw new ProvisionSessionCliError();
  }
  if (
    result?.status !== 'ready'
    || typeof result.session_reference_hash !== 'string'
    || !/^[a-f0-9]{64}$/.test(result.session_reference_hash)
  ) {
    throw new ProvisionSessionCliError();
  }
  const released = Object.freeze({
    session_reference_hash: result.session_reference_hash,
    status: 'ready',
  });
  try {
    output.write(`${JSON.stringify(released)}\n`);
  } catch {
    throw new ProvisionSessionCliError();
  }
  return released;
}

export function assertProvisioningNodeVersion(version = process.versions.node) {
  const major = Number.parseInt(version.split('.')[0], 10);
  if (major !== 24) {
    throw new ProvisionSessionCliError();
  }
}

function parseTimeout(value) {
  if (value === undefined) return DEFAULT_TIMEOUT_MS;
  if (typeof value !== 'string' || !/^\d+$/.test(value)) {
    throw new ProvisionSessionCliError();
  }
  const timeoutMs = Number(value);
  if (
    !Number.isSafeInteger(timeoutMs)
    || timeoutMs < MIN_TIMEOUT_MS
    || timeoutMs > MAX_TIMEOUT_MS
  ) {
    throw new ProvisionSessionCliError();
  }
  return timeoutMs;
}

function isDirectExecution() {
  if (!process.argv[1]) return false;
  return pathToFileURL(process.argv[1]).href === import.meta.url;
}

if (isDirectExecution()) {
  try {
    assertProvisioningNodeVersion();
    await runInteractiveSessionCli(process.env, {
      argumentsValue: process.argv.slice(2),
    });
  } catch {
    process.stderr.write('Tijori interactive session command failed safely.\n');
    process.exitCode = 1;
  }
}
