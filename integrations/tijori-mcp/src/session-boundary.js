import { lstat, realpath } from 'node:fs/promises';
import { basename, dirname, isAbsolute, resolve } from 'node:path';
import process from 'node:process';


export const SESSION_FILE_ENV = 'JARVIS_TIJORI_SESSION_FILE';
export const PROVIDER_CONTRACT_ENV = 'JARVIS_TIJORI_PROVIDER_CONTRACT_VERSION';
export const MAX_SESSION_BYTES = 5_000_000;

const SESSION_FILE_PATTERN = /^[a-f0-9]{64}\.json$/;
const CONTRACT_VERSION_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$/;

export class SessionConfigurationError extends Error {
  constructor() {
    super('Tijori session boundary is not configured safely');
    this.name = 'SessionConfigurationError';
  }
}

export async function loadSessionBoundary(environment = process.env) {
  const sessionFile = environment[SESSION_FILE_ENV];
  const providerContractVersion = environment[PROVIDER_CONTRACT_ENV];
  if (
    typeof sessionFile !== 'string'
    || !isAbsolute(sessionFile)
    || !SESSION_FILE_PATTERN.test(basename(sessionFile))
    || typeof providerContractVersion !== 'string'
    || !CONTRACT_VERSION_PATTERN.test(providerContractVersion)
  ) {
    throw new SessionConfigurationError();
  }

  const normalizedFile = resolve(sessionFile);
  const sessionRoot = dirname(normalizedFile);
  try {
    const [rootInfo, fileInfo, canonicalRoot] = await Promise.all([
      lstat(sessionRoot),
      lstat(normalizedFile),
      realpath(sessionRoot),
    ]);
    const ownerUid = currentUserId();
    if (
      canonicalRoot !== sessionRoot
      || rootInfo.isSymbolicLink()
      || !rootInfo.isDirectory()
      || rootInfo.uid !== ownerUid
      || (rootInfo.mode & 0o077) !== 0
      || fileInfo.isSymbolicLink()
      || !fileInfo.isFile()
      || fileInfo.uid !== ownerUid
      || fileInfo.nlink !== 1
      || (fileInfo.mode & 0o077) !== 0
      || fileInfo.size < 2
      || fileInfo.size > MAX_SESSION_BYTES
    ) {
      throw new SessionConfigurationError();
    }
  } catch (error) {
    if (error instanceof SessionConfigurationError) throw error;
    throw new SessionConfigurationError();
  }

  return Object.freeze({
    authenticated: true,
    providerContractVersion,
    sessionFile: normalizedFile,
  });
}

function currentUserId() {
  if (typeof process.getuid !== 'function') throw new SessionConfigurationError();
  return process.getuid();
}
