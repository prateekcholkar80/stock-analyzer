import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import {
  chmod,
  mkdtemp,
  readFile,
  readdir,
  rm,
  stat,
  symlink,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { createCompanyFilingDownloader } from '../src/company-filing-download.js';


const NOW = new Date('2026-09-14T12:00:00+05:30');
const ISSUER = Object.freeze({
  exchange: 'NSE',
  symbol: 'COFORGE',
  legal_name: 'Coforge Ltd.',
  isin: 'INE591G01017',
  provider_company_id: '242',
  provider_slug: 'niit-technologies-limited',
});
const SOURCE_PAGE_URL = (
  'https://www.tijorifinance.com/company/niit-technologies-limited/'
);
const EARNINGS_URL = (
  'https://files.tijorifinance.com/insight/india/242/'
  + 'Earnings%20Release/ER-Jan26.pdf'
);
const PDF_BYTES = Buffer.from('%PDF-1.7\nfixture body\n%%EOF\n', 'ascii');

function request({
  artifactUrl = EARNINGS_URL,
  category = 'earnings_release',
  sourcePageUrl = SOURCE_PAGE_URL,
  filingReferenceId = referenceId(ISSUER.symbol, category, artifactUrl),
} = {}) {
  return {
    issuer: ISSUER,
    filing_reference: {
      filing_reference_id: filingReferenceId,
      category,
      provider_company_id: '242',
      provider_slug: 'niit-technologies-limited',
      source_page_url: sourcePageUrl,
      artifact_url: artifactUrl,
      discovery_fingerprint: 'a'.repeat(64),
    },
    expected_source_url: artifactUrl,
    expected_provider_company_id: '242',
  };
}

function referenceId(symbol, category, artifactUrl) {
  const digest = createHash('sha256')
    .update(`${category}\n${artifactUrl}`, 'utf8')
    .digest('hex');
  return `filing.${symbol.toLowerCase()}.${digest.slice(0, 32)}`;
}

function browserRunner(observed = {}) {
  return {
    async run(task) {
      return task({
        context() {
          return {
            async cookies(url) {
              observed.cookieUrl = url;
              return [];
            },
          };
        },
      });
    },
  };
}

function syntheticResponse({
  chunks = [PDF_BYTES.subarray(0, 2), PDF_BYTES.subarray(2)],
  status = 200,
  url = EARNINGS_URL,
  contentType = 'application/pdf',
  contentLength = chunks.reduce((total, chunk) => total + chunk.byteLength, 0),
  body = undefined,
} = {}) {
  const stream = body ?? new ReadableStream({
    pull(controller) {
      const chunk = chunks.shift();
      if (chunk === undefined) {
        controller.close();
      } else {
        controller.enqueue(new Uint8Array(chunk));
      }
    },
  });
  const headers = new Headers({ 'content-type': contentType });
  if (contentLength !== null) headers.set('content-length', String(contentLength));
  return { status, url, headers, body: stream };
}

async function stagingDirectory(context) {
  const root = await mkdtemp(join(tmpdir(), 'jarvis-filing-download-'));
  await chmod(root, 0o700);
  context.after(async () => rm(root, { recursive: true, force: true }));
  return root;
}

test('streams a bounded PDF into an atomic content-addressed artifact', async (context) => {
  const root = await stagingDirectory(context);
  const observed = {};
  const expectedHash = createHash('sha256').update(PDF_BYTES).digest('hex');
  const expectedKey = `${expectedHash}.pdf`;
  await writeFile(join(root, expectedKey), 'stale', { mode: 0o600 });
  const downloader = createCompanyFilingDownloader({
    browserRunner: browserRunner(observed),
    stagingDirectory: root,
    fetcher: async (url, options) => {
      observed.url = url;
      observed.options = options;
      return syntheticResponse();
    },
    clock: () => NOW,
  });

  const result = await downloader(request());

  assert.equal(observed.cookieUrl, EARNINGS_URL);
  assert.equal(observed.url, EARNINGS_URL);
  assert.equal(observed.options.method, 'GET');
  assert.equal(observed.options.redirect, 'manual');
  assert.equal(observed.options.headers.referer, SOURCE_PAGE_URL);
  assert.equal(Object.hasOwn(observed.options.headers, 'cookie'), false);
  assert.equal(result.content_sha256, expectedHash);
  assert.equal(result.staged_artifact_key, expectedKey);
  assert.equal(result.byte_length, PDF_BYTES.byteLength);
  assert.equal(result.downloaded_at, NOW.toISOString());
  assert.equal(result.validation_status, 'validated');
  assert.equal(Object.isFrozen(result), true);
  assert.deepEqual(await readdir(root), [expectedKey]);
  assert.deepEqual(await readFile(join(root, expectedKey)), PDF_BYTES);
  assert.equal((await stat(join(root, expectedKey))).mode & 0o077, 0);
  assert.equal(JSON.stringify(result).includes(root), false);
  assert.equal(JSON.stringify(result).includes('path'), false);
});

test('strips a bounded provider preamble before hashing and staging a PDF', async (context) => {
  const root = await stagingDirectory(context);
  const providerPreamble = Buffer.from(
    '<script>console.log(0)</script>',
    'ascii',
  );
  const expectedHash = createHash('sha256').update(PDF_BYTES).digest('hex');
  const downloader = createCompanyFilingDownloader({
    browserRunner: browserRunner(),
    stagingDirectory: root,
    fetcher: async () => syntheticResponse({
      chunks: [
        providerPreamble.subarray(0, 17),
        Buffer.concat([providerPreamble.subarray(17), PDF_BYTES.subarray(0, 3)]),
        PDF_BYTES.subarray(3),
      ],
      contentLength: null,
    }),
    clock: () => NOW,
  });

  const result = await downloader(request());

  assert.equal(result.content_sha256, expectedHash);
  assert.equal(result.byte_length, PDF_BYTES.byteLength);
  assert.deepEqual(
    await readFile(join(root, `${expectedHash}.pdf`)),
    PDF_BYTES,
  );
});

test('rejects unbound source, issuer, category, and reference identifiers before fetch', async (context) => {
  const root = await stagingDirectory(context);
  let fetchCalls = 0;
  const downloader = createCompanyFilingDownloader({
    browserRunner: browserRunner(),
    stagingDirectory: root,
    fetcher: async () => {
      fetchCalls += 1;
      return syntheticResponse();
    },
  });

  const cases = [
    request({
      sourcePageUrl: 'https://www.tijorifinance.com/company/another-company/',
    }),
    request({
      artifactUrl: (
        'https://files.tijorifinance.com/insight/india/999/'
        + 'Earnings%20Release/ER-Jan26.pdf'
      ),
    }),
    request({
      artifactUrl: (
        'https://files.tijorifinance.com/insight/india/242/'
        + 'Conference%20Call/ER-Jan26.pdf'
      ),
    }),
    request({ filingReferenceId: 'filing.coforge.invalid' }),
  ];
  for (const candidate of cases) {
    await assert.rejects(
      () => downloader(candidate),
      /issuer bound|issuer\/category bound|identifier is invalid/,
    );
  }
  assert.equal(fetchCalls, 0);
  assert.deepEqual(await readdir(root), []);
});

test('rejects unapproved hosts before entering the browser session', async (context) => {
  const root = await stagingDirectory(context);
  let browserCalls = 0;
  const downloader = createCompanyFilingDownloader({
    browserRunner: {
      async run() {
        browserCalls += 1;
      },
    },
    stagingDirectory: root,
  });
  const artifactUrl = 'https://example.com/Earnings%20Release/ER-Jan26.pdf';

  await assert.rejects(
    () => downloader(request({ artifactUrl })),
    /outside the approved provider/,
  );
  assert.equal(browserCalls, 0);
});

test('rejects response redirects, statuses, media types, and declared oversize', async (context) => {
  const scenarios = [
    {
      response: { status: 403 },
      error: /did not return a PDF/,
    },
    {
      response: { url: 'https://files.tijorifinance.com/sign-in/' },
      error: /URL changed unexpectedly/,
    },
    {
      response: { contentType: 'text/html' },
      error: /content type is not PDF/,
    },
    {
      response: { contentLength: 101 },
      error: /exceeds the byte limit/,
    },
  ];
  for (const scenario of scenarios) {
    const root = await stagingDirectory(context);
    const downloader = createCompanyFilingDownloader({
      browserRunner: browserRunner(),
      stagingDirectory: root,
      fetcher: async () => syntheticResponse(scenario.response),
      maximumBytes: 100,
    });
    await assert.rejects(() => downloader(request()), scenario.error);
    assert.deepEqual(await readdir(root), []);
  }
});

test('rejects invalid signatures and streamed oversize while removing partial files', async (context) => {
  const scenarios = [
    {
      chunks: [Buffer.from('<html>not a pdf</html>')],
      contentLength: null,
      maximumBytes: 100,
      error: /not a PDF/,
    },
    {
      chunks: [Buffer.alloc(1_025, 32), PDF_BYTES],
      contentLength: null,
      maximumBytes: 2_000,
      error: /not a PDF/,
    },
    {
      chunks: [Buffer.from('%PDF-'), Buffer.alloc(20, 1)],
      contentLength: null,
      maximumBytes: 20,
      error: /exceeds the byte limit/,
    },
  ];
  for (const scenario of scenarios) {
    const root = await stagingDirectory(context);
    const downloader = createCompanyFilingDownloader({
      browserRunner: browserRunner(),
      stagingDirectory: root,
      fetcher: async () => syntheticResponse({
        chunks: [...scenario.chunks],
        contentLength: scenario.contentLength,
      }),
      maximumBytes: scenario.maximumBytes,
    });
    await assert.rejects(() => downloader(request()), scenario.error);
    assert.deepEqual(await readdir(root), []);
  }
});

test('requires an owner-only real directory and rejects symlinked staging', async (context) => {
  const root = await stagingDirectory(context);
  await chmod(root, 0o755);
  const insecure = createCompanyFilingDownloader({
    browserRunner: browserRunner(),
    stagingDirectory: root,
  });
  await assert.rejects(() => insecure(request()), /must be owner-only/);

  await chmod(root, 0o700);
  const link = `${root}-link`;
  await symlink(root, link);
  context.after(async () => rm(link, { force: true }));
  const symlinked = createCompanyFilingDownloader({
    browserRunner: browserRunner(),
    stagingDirectory: link,
  });
  await assert.rejects(
    () => symlinked(request()),
    /must be a directory|cannot use symlinks/,
  );
});

test('rejects unsafe construction and malformed browser contexts', async (context) => {
  const root = await stagingDirectory(context);
  assert.throws(
    () => createCompanyFilingDownloader({
      browserRunner: browserRunner(),
      stagingDirectory: 'relative/path',
    }),
    /must be absolute/,
  );
  assert.throws(
    () => createCompanyFilingDownloader({
      browserRunner: browserRunner(),
      stagingDirectory: root,
      maximumBytes: 100_000_001,
    }),
    /byte limit is invalid/,
  );

  const downloader = createCompanyFilingDownloader({
    browserRunner: { run: async (task) => task({}) },
    stagingDirectory: root,
  });
  await assert.rejects(
    () => downloader(request()),
    /requires a browser context/,
  );
  assert.deepEqual(await readdir(root), []);
});
