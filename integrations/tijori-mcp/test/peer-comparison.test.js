import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createPeerComparisonSnapshotExtractor,
  normalizePeerComparison,
} from '../src/peer-comparison.js';

const NOW = new Date('2026-09-01T10:00:00+05:30');
const ISSUER = Object.freeze({
  exchange: 'NSE',
  symbol: 'COFORGE',
  legal_name: 'Coforge Ltd.',
  provider_company_id: '123',
  provider_slug: 'niit-technologies-limited',
});
const HEADERS = [
  'Peer Name', 'Latest Price', 'PE', 'PEG', 'Market Cap(Cr)',
  'Prom Holding(%)', 'YoY Qtly Sales(%)', 'ROCE (%)', 'ROE (%)',
];

function snapshot() {
  return {
    headers: [...HEADERS],
    rows: [
      {
        name: 'Coforge',
        href: '/company/niit-technologies-limited',
        values: ['₹1,450.50', '24.1', '1.2', '96,000 Cr', '—', '18.2%', '22.1%', '16.3%'],
      },
      {
        name: 'Persistent Systems',
        href: '/company/persistent-systems-limited',
        values: ['₹5,100', '55.4', '2.0', '82,500 Cr', '31.2%', '0', '28.4%', '24.5%'],
      },
    ],
  };
}

test('normalizes a complete cross-sectional peer matrix', () => {
  const result = normalizePeerComparison(snapshot(), { issuer: ISSUER }, NOW);

  assert.equal(result.schema_version, 'tijori.peer_comparison.v1');
  assert.equal(result.document_type, 'peer_comparison');
  assert.equal(result.observation_date, '2026-09-01');
  assert.equal(result.metrics.length, 8);
  assert.equal(result.peers.length, 2);
  assert.equal(result.peers[0].is_subject, true);
  assert.equal(result.peers[1].is_subject, false);
  assert.equal(result.peers[0].values[4].source_value, null);
  assert.equal(result.peers[0].values[4].availability_status, 'unknown');
  assert.equal(result.peers[1].values[5].source_value, '0');
  assert.equal(result.extraction.cell_count, 16);
  assert.equal(Object.isFrozen(result.peers[0]), true);
});

test('extracts the rendered provider table after confirming the issuer path', async () => {
  const observed = {};
  const page = {
    async goto(url, options) {
      observed.url = url;
      observed.options = options;
      return { url: () => url };
    },
    async waitForSelector(selector, options) {
      observed.selector = selector;
      observed.selectorOptions = options;
    },
    async evaluate(callback) {
      observed.callback = callback;
      return snapshot();
    },
  };
  const extractor = createPeerComparisonSnapshotExtractor({
    browserRunner: { run: async (task) => task(page) },
    clock: () => NOW,
  });

  const result = await extractor({ issuer: ISSUER });

  assert.equal(observed.url, `${'https://www.tijorifinance.com'}/company/${ISSUER.provider_slug}/`);
  assert.equal(observed.options.waitUntil, 'domcontentloaded');
  assert.equal(observed.selector, '#competitors #peers_table');
  assert.equal(result.peers[0].legal_name, 'Coforge');
});

test('fails closed for unsupported metrics, duplicate peers, and a missing subject', () => {
  const unsupported = snapshot();
  unsupported.headers[1] = 'Secret Alpha Score';
  assert.throws(
    () => normalizePeerComparison(unsupported, { issuer: ISSUER }, NOW),
    /unsupported metric/,
  );

  const duplicate = snapshot();
  duplicate.rows[1].href = duplicate.rows[0].href;
  assert.throws(
    () => normalizePeerComparison(duplicate, { issuer: ISSUER }, NOW),
    /peers must be unique/,
  );

  const missingSubject = snapshot();
  missingSubject.rows[0].href = '/company/another-company';
  assert.throws(
    () => normalizePeerComparison(missingSubject, { issuer: ISSUER }, NOW),
    /identify the subject exactly once/,
  );
});

test('preserves missing separately from zero and rejects malformed values', () => {
  const malformed = snapshot();
  malformed.rows[0].values[1] = 'twenty four';
  assert.throws(
    () => normalizePeerComparison(malformed, { issuer: ISSUER }, NOW),
    /numeric or explicitly missing/,
  );

  const wrongWidth = snapshot();
  wrongWidth.rows[0].values.pop();
  assert.throws(
    () => normalizePeerComparison(wrongWidth, { issuer: ISSUER }, NOW),
    /cover every metric/,
  );
});

test('rejects redirects, unsafe links, extra request fields, and invalid clocks', async () => {
  const redirected = createPeerComparisonSnapshotExtractor({
    browserRunner: {
      run: async (task) => task({
        goto: async () => ({ url: () => 'https://www.tijorifinance.com/dashboard/' }),
      }),
    },
    clock: () => NOW,
  });
  await assert.rejects(
    () => redirected({ issuer: ISSUER }),
    /did not match the resolved issuer/,
  );

  const unsafe = snapshot();
  unsafe.rows[1].href = 'https://example.com/company/persistent-systems-limited';
  assert.throws(
    () => normalizePeerComparison(unsafe, { issuer: ISSUER }, NOW),
    /outside the provider/,
  );
  assert.throws(
    () => normalizePeerComparison(snapshot(), { issuer: ISSUER, raw: true }, NOW),
    /unsupported fields/,
  );
  assert.throws(
    () => normalizePeerComparison(snapshot(), { issuer: ISSUER }, new Date('invalid')),
    /retrieval time is invalid/,
  );
});
