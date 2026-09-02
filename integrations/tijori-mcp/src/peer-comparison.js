const BASE_URL = 'https://www.tijorifinance.com';
const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{0,239}$/;
const METRIC_KEY_PATTERN = /^[a-z][a-z0-9_]{0,79}$/;
const MAX_PEERS = 50;
const MAX_METRICS = 30;
const MAX_CELLS = 1_500;

const METRICS = Object.freeze({
  'latest price': metric('latest_price', 'Latest Price', 'monetary', 'INR'),
  pe: metric('pe', 'P/E', 'ratio', 'ratio'),
  peg: metric('peg', 'PEG', 'ratio', 'ratio'),
  'market cap cr': metric('market_cap', 'Market Cap', 'monetary', 'INR crore'),
  'prom holding': metric('promoter_holding', 'Promoter Holding', 'percentage', 'percent'),
  'yoy qtly sales': metric('quarterly_sales_growth_yoy', 'Quarterly Sales Growth YoY', 'percentage', 'percent'),
  roce: metric('roce', 'ROCE', 'percentage', 'percent'),
  roe: metric('roe', 'ROE', 'percentage', 'percent'),
});

/** Extract one complete cross-sectional peer-comparison snapshot. */
export function createPeerComparisonSnapshotExtractor({ browserRunner, clock = () => new Date() }) {
  if (browserRunner === null || typeof browserRunner?.run !== 'function') {
    throw new TypeError('Peer Comparison extraction requires a browser runner');
  }
  if (typeof clock !== 'function') {
    throw new TypeError('Peer Comparison extraction clock must be callable');
  }
  return async function extractPeerComparison(argumentsValue) {
    const request = validateRequest(argumentsValue);
    const retrievedAt = clock();
    if (!(retrievedAt instanceof Date) || Number.isNaN(retrievedAt.valueOf())) {
      throw new TypeError('Peer Comparison extraction clock returned an invalid date');
    }
    return browserRunner.run(async (page) => {
      const response = await page.goto(
        `${BASE_URL}/company/${request.issuer.provider_slug}/`,
        { timeout: 20_000, waitUntil: 'domcontentloaded' },
      );
      if (!responseMatchesIssuerPath(response, request.issuer.provider_slug)) {
        throw new TypeError('Peer Comparison response did not match the resolved issuer');
      }
      await page.waitForSelector('#competitors #peers_table', { timeout: 20_000 });
      const raw = await page.evaluate(readPeerComparisonSnapshot);
      return normalizePeerComparison(raw, request, retrievedAt);
    });
  };
}

/** Normalize the bounded rendered matrix into a provider JSON document. */
export function normalizePeerComparison(raw, requestValue, retrievedAt) {
  const request = validateRequest(requestValue);
  if (!(retrievedAt instanceof Date) || Number.isNaN(retrievedAt.valueOf())) {
    throw new TypeError('Peer Comparison retrieval time is invalid');
  }
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new TypeError('Peer Comparison snapshot must be an object');
  }
  if (!Array.isArray(raw.headers) || !Array.isArray(raw.rows)) {
    throw new TypeError('Peer Comparison snapshot requires headers and rows');
  }
  if (raw.rows.length === 0 || raw.rows.length > MAX_PEERS) {
    throw new RangeError('Peer Comparison peer count is outside its safety bound');
  }
  const headers = raw.headers.map((value) => boundedString(value, 'Peer Comparison header', 120));
  if (headers.length < 2 || headers.length - 1 > MAX_METRICS) {
    throw new RangeError('Peer Comparison metric count is outside its safety bound');
  }
  if (comparable(headers[0]) !== 'peer name') {
    throw new TypeError('Peer Comparison first column must identify the peer');
  }
  const metrics = headers.slice(1).map((label, displayOrder) => {
    const definition = METRICS[comparable(label)];
    if (definition === undefined) {
      throw new TypeError('Peer Comparison contains an unsupported metric');
    }
    return { ...definition, source_label: label, display_order: displayOrder };
  });
  requireUnique(metrics.map(({ metric_key }) => metric_key), 'Peer Comparison metrics');
  if (raw.rows.length * metrics.length > MAX_CELLS) {
    throw new RangeError('Peer Comparison exceeds its cell safety bound');
  }
  const peers = raw.rows.map((row, displayOrder) => normalizePeer(
    row,
    metrics,
    request.issuer.provider_slug,
    displayOrder,
  ));
  requireUnique(peers.map(({ provider_slug }) => provider_slug), 'Peer Comparison peers');
  if (peers.filter(({ is_subject }) => is_subject).length !== 1) {
    throw new TypeError('Peer Comparison must identify the subject exactly once');
  }
  return Object.freeze({
    schema_version: 'tijori.peer_comparison.v1',
    document_type: 'peer_comparison',
    issuer: Object.freeze({ ...request.issuer }),
    source: Object.freeze({
      provider: 'tijori',
      location: `${BASE_URL}/company/${request.issuer.provider_slug}/`,
      retrieved_at: retrievedAt.toISOString(),
    }),
    observation_date: istDate(retrievedAt),
    metrics: Object.freeze(metrics.map((value) => Object.freeze(value))),
    peers: Object.freeze(peers),
    extraction: Object.freeze({
      metric_count: metrics.length,
      peer_count: peers.length,
      cell_count: metrics.length * peers.length,
      status: 'complete',
    }),
  });
}

function readPeerComparisonSnapshot() {
  const table = document.querySelector('#competitors #peers_table');
  if (!(table instanceof HTMLTableElement)) {
    throw new TypeError('Peer Comparison table is unavailable');
  }
  const headers = Array.from(table.querySelectorAll('thead th'))
    .map((cell) => cell.textContent?.trim().replace(/\s+/g, ' ') ?? '');
  const rows = Array.from(table.querySelectorAll('tbody tr')).map((row) => {
    const cells = Array.from(row.querySelectorAll(':scope > th, :scope > td'));
    const peerLink = cells[0]?.querySelector('a[href]');
    return {
      name: peerLink?.textContent?.trim().replace(/\s+/g, ' ')
        ?? cells[0]?.textContent?.trim().replace(/\s+/g, ' '),
      href: peerLink?.getAttribute('href') ?? null,
      values: cells.slice(1).map((cell) => (
        cell.textContent?.trim().replace(/\s+/g, ' ') ?? ''
      )),
    };
  });
  return { headers, rows };
}

function normalizePeer(row, metrics, subjectSlug, displayOrder) {
  if (row === null || typeof row !== 'object' || Array.isArray(row)) {
    throw new TypeError('Peer Comparison row must be an object');
  }
  const name = boundedString(row.name, 'Peer Comparison company name', 300);
  const providerSlug = slugFromHref(row.href);
  if (!Array.isArray(row.values) || row.values.length !== metrics.length) {
    throw new TypeError('Peer Comparison row must cover every metric');
  }
  return Object.freeze({
    peer_key: `peer_${providerSlug.replaceAll('-', '_')}`,
    legal_name: name,
    provider_slug: providerSlug,
    is_subject: providerSlug === subjectSlug,
    display_order: displayOrder,
    values: Object.freeze(row.values.map((value, index) => normalizeCell(
      value,
      metrics[index].metric_key,
    ))),
  });
}

function normalizeCell(value, metricKey) {
  const sourceValue = boundedString(value, 'Peer Comparison value', 120, { allowBlank: true });
  const missing = sourceValue === '' || /^(?:-|—|na|n\/a)$/i.test(sourceValue);
  if (!METRIC_KEY_PATTERN.test(metricKey)) {
    throw new TypeError('Peer Comparison metric key is invalid');
  }
  if (!missing && parseNumeric(sourceValue) === null) {
    throw new TypeError('Peer Comparison value must be numeric or explicitly missing');
  }
  return Object.freeze({
    metric_key: metricKey,
    source_value: missing ? null : sourceValue,
    availability_status: missing ? 'unknown' : 'available',
  });
}

function validateRequest(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError('Peer Comparison request must be an object');
  }
  const keys = Object.keys(value).sort();
  if (keys.length !== 1 || keys[0] !== 'issuer') {
    throw new TypeError('Peer Comparison request contains unsupported fields');
  }
  const issuer = value.issuer;
  if (issuer === null || typeof issuer !== 'object' || Array.isArray(issuer)) {
    throw new TypeError('Peer Comparison request requires an issuer');
  }
  const providerSlug = boundedString(
    issuer.provider_slug,
    'Peer Comparison provider slug',
    240,
  ).toLowerCase();
  if (!SLUG_PATTERN.test(providerSlug)) {
    throw new TypeError('Peer Comparison provider slug is invalid');
  }
  for (const field of ['exchange', 'symbol', 'legal_name', 'provider_company_id']) {
    boundedString(issuer[field], `Peer Comparison issuer ${field}`, 300);
  }
  return { issuer: { ...issuer, provider_slug: providerSlug } };
}

function responseMatchesIssuerPath(response, slug) {
  if (response === null || typeof response?.url !== 'function') return false;
  try {
    const url = new URL(response.url());
    return url.origin === BASE_URL && url.pathname === `/company/${slug}/`;
  } catch {
    return false;
  }
}

function slugFromHref(value) {
  const href = boundedString(value, 'Peer Comparison company link', 500);
  let url;
  try {
    url = new URL(href, BASE_URL);
  } catch {
    throw new TypeError('Peer Comparison company link is invalid');
  }
  const match = /^\/company\/([a-z0-9][a-z0-9-]{0,239})\/?$/.exec(url.pathname);
  if (url.origin !== BASE_URL || match === null) {
    throw new TypeError('Peer Comparison company link is outside the provider');
  }
  return match[1];
}

function parseNumeric(value) {
  const normalized = value
    .replaceAll(',', '')
    .replace(/[₹%]/g, '')
    .replace(/\b(?:cr|x)\b/gi, '')
    .trim();
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(normalized)) return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function boundedString(value, label, maximum, { allowBlank = false } = {}) {
  if (typeof value !== 'string') throw new TypeError(`${label} must be text`);
  const normalized = value.trim().replace(/\s+/g, ' ');
  if ((!allowBlank && normalized.length === 0) || normalized.length > maximum) {
    throw new TypeError(`${label} is outside its safety bound`);
  }
  return normalized;
}

function comparable(value) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function requireUnique(values, label) {
  if (values.length !== new Set(values).size) {
    throw new TypeError(`${label} must be unique`);
  }
}

function metric(metricKey, label, valueKind, sourceUnit) {
  return Object.freeze({
    metric_key: metricKey,
    standardized_label: label,
    value_kind: valueKind,
    source_unit: sourceUnit,
  });
}

function istDate(value) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(value);
  const selected = Object.fromEntries(parts.map(({ type, value: part }) => [type, part]));
  return `${selected.year}-${selected.month}-${selected.day}`;
}
