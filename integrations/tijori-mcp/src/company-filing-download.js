import { createHash, randomUUID } from 'node:crypto';
import {
  lstat,
  open,
  realpath,
  rename,
  stat,
  unlink,
} from 'node:fs/promises';
import { isAbsolute, join, resolve } from 'node:path';

import { validateDownloadCompanyFilingArguments } from './tool-inputs.js';


const COMPANY_ORIGIN = 'https://www.tijorifinance.com';
const ARTIFACT_ORIGIN = 'https://files.tijorifinance.com';
const PDF_SIGNATURE = Buffer.from('%PDF-', 'ascii');
const MINIMUM_PDF_BYTES = PDF_SIGNATURE.length;
const MAXIMUM_PDF_PREAMBLE_BYTES = 1_024;
const MAXIMUM_PDF_BYTES = 100_000_000;
const CATEGORY_DIRECTORIES = Object.freeze({
  earnings_release: 'Earnings Release',
  conference_call: 'Conference Call',
});

/**
 * Stage one exact discovered PDF through the authenticated browser session.
 *
 * The returned artifact key is content-addressed and contains no filesystem
 * path. The caller owns the separately configured staging-root boundary.
 */
export function createCompanyFilingDownloader({
  browserRunner,
  stagingDirectory,
  fetcher = globalThis.fetch,
  clock = () => new Date(),
  maximumBytes = MAXIMUM_PDF_BYTES,
  timeoutMs = 30_000,
}) {
  if (browserRunner === null || typeof browserRunner?.run !== 'function') {
    throw new TypeError('Company Filing download requires a browser runner');
  }
  if (typeof fetcher !== 'function') {
    throw new TypeError('Company Filing download requires a fetch implementation');
  }
  if (typeof clock !== 'function') {
    throw new TypeError('Company Filing download clock must be callable');
  }
  if (!isAbsolute(stagingDirectory ?? '')) {
    throw new TypeError('Company Filing staging directory must be absolute');
  }
  if (
    !Number.isInteger(maximumBytes)
    || maximumBytes < MINIMUM_PDF_BYTES
    || maximumBytes > MAXIMUM_PDF_BYTES
  ) {
    throw new TypeError('Company Filing byte limit is invalid');
  }
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 120_000) {
    throw new TypeError('Company Filing timeout is invalid');
  }

  return async function downloadCompanyFiling(argumentsValue) {
    const request = validateDownloadCompanyFilingArguments(argumentsValue);
    validateReferenceBinding(request);
    const ownerDirectory = await validateOwnerOnlyDirectory(stagingDirectory);

    return browserRunner.run(async (page) => {
      const cookieHeader = await authenticatedCookieHeader(
        page,
        request.expected_source_url,
      );
      return stageResponse({
        request,
        ownerDirectory,
        fetcher,
        cookieHeader,
        clock,
        maximumBytes,
        timeoutMs,
      });
    });
  };
}

async function stageResponse({
  request,
  ownerDirectory,
  fetcher,
  cookieHeader,
  clock,
  maximumBytes,
  timeoutMs,
}) {
  const temporaryPath = join(ownerDirectory, `.partial-${randomUUID()}`);
  let temporaryHandle;
  const abortController = new AbortController();
  const timeout = setTimeout(() => abortController.abort(), timeoutMs);
  try {
    temporaryHandle = await open(temporaryPath, 'wx', 0o600);
    const headers = {
      accept: 'application/pdf',
      referer: request.filing_reference.source_page_url,
    };
    if (cookieHeader !== '') headers.cookie = cookieHeader;
    const response = await fetcher(request.expected_source_url, {
      method: 'GET',
      headers,
      redirect: 'manual',
      signal: abortController.signal,
    });
    validateResponse(response, request.expected_source_url, maximumBytes);

    const digest = createHash('sha256');
    let receivedByteLength = 0;
    let pdfByteLength = 0;
    let headerProbe = Buffer.alloc(0);
    let pdfStarted = false;
    const reader = response.body.getReader();
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (!(value instanceof Uint8Array)) {
          throw new TypeError('Company Filing response emitted an invalid chunk');
        }
        const chunk = Buffer.from(value);
        receivedByteLength += chunk.byteLength;
        if (receivedByteLength > maximumBytes) {
          throw new RangeError('Company Filing response exceeds the byte limit');
        }

        let pdfChunk = chunk;
        if (!pdfStarted) {
          headerProbe = Buffer.concat([headerProbe, chunk]);
          const signatureOffset = headerProbe.indexOf(PDF_SIGNATURE);
          if (signatureOffset === -1) {
            if (
              headerProbe.byteLength
              > MAXIMUM_PDF_PREAMBLE_BYTES + PDF_SIGNATURE.byteLength - 1
            ) {
              throw new TypeError('Company Filing response is not a PDF');
            }
            continue;
          }
          if (signatureOffset > MAXIMUM_PDF_PREAMBLE_BYTES) {
            throw new TypeError('Company Filing response is not a PDF');
          }
          pdfStarted = true;
          pdfChunk = headerProbe.subarray(signatureOffset);
          headerProbe = Buffer.alloc(0);
        }
        pdfByteLength += pdfChunk.byteLength;
        digest.update(pdfChunk);
        await temporaryHandle.write(pdfChunk);
      }
    } finally {
      reader.releaseLock();
    }
    if (!pdfStarted || pdfByteLength < MINIMUM_PDF_BYTES) {
      throw new TypeError('Company Filing response is not a PDF');
    }
    await temporaryHandle.sync();
    await temporaryHandle.close();
    temporaryHandle = undefined;

    const contentSha256 = digest.digest('hex');
    const downloadedAt = clock();
    requireValidDate(downloadedAt);
    await validateStagedArtifact(temporaryPath, pdfByteLength);
    const stagedArtifactKey = `${contentSha256}.pdf`;
    await rename(temporaryPath, join(ownerDirectory, stagedArtifactKey));
    return deepFreeze({
      schema_version: 'tijori.company_filing_download.v1',
      filing_reference_id: request.filing_reference.filing_reference_id,
      category: request.filing_reference.category,
      provider_company_id: request.expected_provider_company_id,
      provider_slug: request.issuer.provider_slug,
      source_page_url: request.filing_reference.source_page_url,
      artifact_url: request.expected_source_url,
      discovery_fingerprint: request.filing_reference.discovery_fingerprint,
      content_sha256: contentSha256,
      byte_length: pdfByteLength,
      content_type: 'application/pdf',
      staged_artifact_key: stagedArtifactKey,
      downloaded_at: downloadedAt.toISOString(),
      validation_status: 'validated',
    });
  } finally {
    clearTimeout(timeout);
    if (temporaryHandle !== undefined) {
      try {
        await temporaryHandle.close();
      } catch {
        // Cleanup errors must not replace the validation failure.
      }
    }
    try {
      await unlink(temporaryPath);
    } catch (error) {
      if (error?.code !== 'ENOENT') {
        throw new TypeError('Company Filing temporary artifact cleanup failed');
      }
    }
  }
}

function validateReferenceBinding(request) {
  const { issuer, filing_reference: reference } = request;
  const sourcePage = parseUrl(reference.source_page_url, 'source page');
  if (
    sourcePage.origin !== COMPANY_ORIGIN
    || sourcePage.pathname !== `/company/${issuer.provider_slug}/`
    || sourcePage.search
    || sourcePage.hash
    || sourcePage.username
    || sourcePage.password
  ) {
    throw new TypeError('Company Filing source page is not issuer bound');
  }

  const artifact = parseUrl(request.expected_source_url, 'artifact');
  if (
    artifact.origin !== ARTIFACT_ORIGIN
    || artifact.search
    || artifact.hash
    || artifact.username
    || artifact.password
  ) {
    throw new TypeError('Company Filing artifact is outside the approved provider');
  }
  let decodedPath;
  try {
    decodedPath = decodeURIComponent(artifact.pathname);
  } catch {
    throw new TypeError('Company Filing artifact URL encoding is invalid');
  }
  const directory = CATEGORY_DIRECTORIES[reference.category];
  const prefix = (
    `/insight/india/${request.expected_provider_company_id}/${directory}/`
  );
  const filename = decodedPath.startsWith(prefix)
    ? decodedPath.slice(prefix.length)
    : '';
  if (
    !filename
    || filename.length > 300
    || filename.includes('/')
    || !/\.pdf$/i.test(filename)
    || [...filename].some((character) => character.charCodeAt(0) < 32)
  ) {
    throw new TypeError('Company Filing artifact is not issuer/category bound');
  }

  const expectedReferenceId = filingReferenceId(
    issuer.symbol,
    reference.category,
    artifact.toString(),
  );
  if (reference.filing_reference_id !== expectedReferenceId) {
    throw new TypeError('Company Filing reference identifier is invalid');
  }
}

async function validateOwnerOnlyDirectory(value) {
  const configuredPath = resolve(value);
  const [metadata, canonicalPath] = await Promise.all([
    lstat(configuredPath),
    realpath(configuredPath),
  ]);
  if (!metadata.isDirectory() || metadata.isSymbolicLink()) {
    throw new TypeError('Company Filing staging location must be a directory');
  }
  if ((metadata.mode & 0o777) !== 0o700) {
    throw new TypeError('Company Filing staging directory must be owner-only');
  }
  if (typeof process.getuid === 'function' && metadata.uid !== process.getuid()) {
    throw new TypeError('Company Filing staging directory owner is invalid');
  }
  return canonicalPath;
}

async function authenticatedCookieHeader(page, artifactUrl) {
  const context = typeof page?.context === 'function' ? page.context() : null;
  if (context === null || typeof context?.cookies !== 'function') {
    throw new TypeError('Company Filing download requires a browser context');
  }
  const cookies = await context.cookies(artifactUrl);
  if (!Array.isArray(cookies) || cookies.length > 100) {
    throw new TypeError('Company Filing browser cookies are invalid');
  }
  return cookies.map((cookie) => {
    if (
      cookie === null
      || typeof cookie !== 'object'
      || typeof cookie.name !== 'string'
      || typeof cookie.value !== 'string'
      || !cookie.name
      || /[;=\r\n]/.test(cookie.name)
      || /[;\r\n]/.test(cookie.value)
    ) {
      throw new TypeError('Company Filing browser cookie is invalid');
    }
    return `${cookie.name}=${cookie.value}`;
  }).join('; ');
}

function validateResponse(response, expectedUrl, maximumBytes) {
  if (response === null || typeof response !== 'object') {
    throw new TypeError('Company Filing response is invalid');
  }
  if (response.status !== 200) {
    throw new TypeError('Company Filing provider did not return a PDF');
  }
  if (typeof response.url !== 'string' || response.url !== expectedUrl) {
    throw new TypeError('Company Filing response URL changed unexpectedly');
  }
  if (response.headers === null || typeof response.headers?.get !== 'function') {
    throw new TypeError('Company Filing response headers are invalid');
  }
  const contentType = response.headers.get('content-type') ?? '';
  if (!/^application\/pdf(?:\s*;|$)/i.test(contentType)) {
    throw new TypeError('Company Filing response content type is not PDF');
  }
  const contentLength = response.headers.get('content-length');
  if (contentLength !== null) {
    if (!/^\d+$/.test(contentLength)) {
      throw new TypeError('Company Filing content length is invalid');
    }
    if (Number(contentLength) > maximumBytes) {
      throw new RangeError('Company Filing response exceeds the byte limit');
    }
  }
  if (response.body === null || typeof response.body?.getReader !== 'function') {
    throw new TypeError('Company Filing response body is unavailable');
  }
}

async function validateStagedArtifact(path, expectedBytes) {
  const metadata = await stat(path);
  if (
    !metadata.isFile()
    || metadata.size !== expectedBytes
    || (metadata.mode & 0o077) !== 0
  ) {
    throw new TypeError('Company Filing staged artifact validation failed');
  }
}

function filingReferenceId(symbol, category, artifactUrl) {
  const digest = createHash('sha256')
    .update(`${category}\n${artifactUrl}`, 'utf8')
    .digest('hex');
  return `filing.${symbol.toLowerCase()}.${digest.slice(0, 32)}`;
}

function parseUrl(value, field) {
  try {
    return new URL(value);
  } catch {
    throw new TypeError(`Company Filing ${field} URL is invalid`);
  }
}

function requireValidDate(value) {
  if (!(value instanceof Date) || Number.isNaN(value.valueOf())) {
    throw new TypeError('Company Filing download clock returned an invalid date');
  }
}

function deepFreeze(value) {
  if (value !== null && typeof value === 'object') {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}
