import assert from 'node:assert/strict';
import test, { after, before } from 'node:test';

import { chromium } from 'playwright';

import {
  createCompanyFilingsDiscovery,
  normalizeCompanyFilings,
} from '../src/company-filings.js';


const NOW = new Date('2026-09-14T10:00:00+05:30');
const ISSUER = Object.freeze({
  exchange: 'NSE',
  symbol: 'COFORGE',
  legal_name: 'Coforge Ltd.',
  isin: 'INE591G01017',
  provider_company_id: '242',
  provider_slug: 'niit-technologies-limited',
});
const SOURCE_URL = (
  'https://www.tijorifinance.com/company/niit-technologies-limited/'
);
let browser;

before(async () => {
  browser = await chromium.launch({ headless: true });
});

after(async () => {
  await browser?.close();
});

function fixtureHtml({
  earningsJanuaryUrl = (
    'https://files.tijorifinance.com/insight/india/242/'
    + 'Earnings%20Release/ER-Jan26.pdf'
  ),
  earningsLabel = 'Earnings Release',
  earningsClasses = 'name__cell earnings-release',
  conferenceCallJanuaryUrl = (
    'https://files.tijorifinance.com/insight/india/242/'
    + 'Conference%20Call/CC-Jan26.pdf'
  ),
} = {}) {
  return `<!doctype html>
    <html><body><section id="company_filings">
      <table id="companyFilings">
        <thead><tr>
          <th class="name__cell"></th><th class="paginate__cell"></th>
          <th class="data__cell">2026</th><th class="data__cell">2025</th>
          <th class="paginate__cell"></th>
        </tr></thead>
        <tbody>
          <tr>
            <td class="name__cell annual-report">Annual Report</td>
            <td class="paginate__cell"></td>
            <td class="data__cell">
              <a href="https://files.tijorifinance.com/insight/india/242/Annual%20Report/AR-26.pdf"><span>FY26</span></a>
            </td>
            <td class="data__cell"><span class="disabled">FY25</span></td>
            <td class="paginate__cell"></td>
          </tr>
          <tr>
            <td class="${earningsClasses}">${earningsLabel}</td>
            <td class="paginate__cell"></td>
            <td class="data__cell">
              <a href="${earningsJanuaryUrl}"><span>Jan</span></a>
              <a href="https://files.tijorifinance.com/insight/india/242/Earnings%20Release/ER-Jul26.pdf"><span>Jul</span></a>
              <a href="https://files.tijorifinance.com/insight/india/242/Earnings%20Release/ER-Jul26-Revised.pdf"><span>Jul</span></a>
            </td>
            <td class="data__cell"><span class="disabled">Mar</span></td>
            <td class="paginate__cell"></td>
          </tr>
          <tr>
            <td class="name__cell conference-call">Conference Call</td>
            <td class="paginate__cell"></td>
            <td class="data__cell">
              <a href="${conferenceCallJanuaryUrl}"><span>Jan</span></a>
            </td>
            <td class="data__cell"></td>
            <td class="paginate__cell"></td>
          </tr>
        </tbody>
      </table>
    </section></body></html>`;
}

function browserRunnerFor(html, { responseUrl = SOURCE_URL } = {}) {
  return {
    async run(task) {
      const page = await browser.newPage();
      await page.route(SOURCE_URL, async (route) => {
        await route.fulfill({
          status: 200,
          contentType: 'text/html',
          body: html,
        });
      });
      if (responseUrl !== SOURCE_URL) {
        page.goto = async () => ({ url: () => responseUrl });
      }
      try {
        return await task(page);
      } finally {
        await page.close();
      }
    },
  };
}

function discoveryFor(html, options = {}) {
  return createCompanyFilingsDiscovery({
    browserRunner: browserRunnerFor(html, options),
    clock: () => NOW,
  });
}

function rawSnapshot() {
  return {
    provider_years: ['2026', '2025'],
    rows: [
      {
        label: 'Earnings Release',
        class_names: ['name__cell', 'earnings-release'],
        groups: [
          {
            provider_year: '2026',
            entries: [{
              kind: 'available',
              label: 'Jan',
              href: (
                'https://files.tijorifinance.com/insight/india/242/'
                + 'Earnings%20Release/ER-Jan26.pdf'
              ),
            }],
          },
          {
            provider_year: '2025',
            entries: [{ kind: 'unavailable', label: 'Mar', href: null }],
          },
        ],
      },
      {
        label: 'Conference Call',
        class_names: ['name__cell', 'conference-call'],
        groups: [
          {
            provider_year: '2026',
            entries: [{
              kind: 'available',
              label: 'Jan',
              href: (
                'https://files.tijorifinance.com/insight/india/242/'
                + 'Conference%20Call/CC-Jan26.pdf'
              ),
            }],
          },
          { provider_year: '2025', entries: [] },
        ],
      },
    ],
  };
}

test('discovers multiple and revised documents from a sanitized HTML fixture', async () => {
  const result = await discoveryFor(fixtureHtml())({ issuer: ISSUER });

  assert.equal(result.schema_version, 'tijori.company_filings.v1');
  assert.equal(result.filings.length, 4);
  assert.equal(result.filings.filter(({ provider_label }) => provider_label === 'Jul').length, 2);
  assert.equal(new Set(result.filings.map(({ filing_reference_id }) => filing_reference_id)).size, 4);
  assert.match(result.discovery_fingerprint, /^[a-f0-9]{64}$/);
  assert.equal(
    result.filings.every(({ discovery_fingerprint }) => (
      discovery_fingerprint === result.discovery_fingerprint
    )),
    true,
  );
  assert.equal(result.filings[0].period.state, 'unclassified');
  assert.equal(result.filings[0].period.fiscal_quarter, null);
  assert.equal(result.coverage.find((item) => (
    item.category === 'earnings_release' && item.provider_year === 2025
  )).status, 'unavailable');
  assert.deepEqual(result.unavailable_period_labels, [
    'earnings_release:Mar 2025',
    'conference_call:2025',
  ]);
  assert.equal(result.filings.some(({ category }) => category === 'annual_report'), false);
  assert.equal(Object.isFrozen(result.filings[0].period), true);
});

test('applies category, calendar-month, and result bounds without guessing fiscal year', async () => {
  const result = await discoveryFor(fixtureHtml())({
    issuer: ISSUER,
    categories: ['earnings_release'],
    from_fiscal_year: 2026,
    through_date: '2026-01-31',
    max_documents: 1,
  });

  assert.equal(result.filings.length, 1);
  assert.equal(result.filings[0].provider_label, 'Jan');
  assert.equal(result.filings[0].period.fiscal_year, null);
  assert.equal(result.extraction.truncated, false);
  assert.match(result.limitations.join(' '), /fiscal-year lower bound was not applied/);
  assert.match(result.limitations.join(' '), /calendar month only/);
});

test('preserves absent links as explicit coverage instead of inventing references', async () => {
  const result = await discoveryFor(fixtureHtml())({
    issuer: ISSUER,
    categories: ['conference_call'],
  });

  assert.equal(result.filings.length, 1);
  assert.deepEqual(result.coverage, [
    {
      category: 'conference_call',
      provider_year: 2026,
      status: 'available',
      document_count: 1,
      unavailable_labels: [],
    },
    {
      category: 'conference_call',
      provider_year: 2025,
      status: 'unavailable',
      document_count: 0,
      unavailable_labels: [],
    },
  ]);
});

test('rejects duplicate artifact URLs and unexpected domains', async () => {
  await assert.rejects(
    () => discoveryFor(fixtureHtml({
      conferenceCallJanuaryUrl: (
        'https://files.tijorifinance.com/insight/india/242/'
        + 'Earnings%20Release/ER-Jan26.pdf'
      ),
    }))({ issuer: ISSUER }),
    /issuer\/category bound/,
  );

  await assert.rejects(
    () => discoveryFor(fixtureHtml({
      earningsJanuaryUrl: 'https://example.com/ER-Jan26.pdf',
    }))({ issuer: ISSUER }),
    /outside the approved provider/,
  );

  const duplicate = rawSnapshot();
  duplicate.rows[1].groups[0].entries[0].href = (
    duplicate.rows[0].groups[0].entries[0].href
  );
  duplicate.rows[1].groups[0].entries[0].label = 'Jan';
  assert.throws(
    () => normalizeCompanyFilings(duplicate, { issuer: ISSUER }, NOW),
    /issuer\/category bound/,
  );

  const withinCategoryDuplicate = rawSnapshot();
  withinCategoryDuplicate.rows[0].groups[0].entries.push({
    ...withinCategoryDuplicate.rows[0].groups[0].entries[0],
  });
  assert.throws(
    () => normalizeCompanyFilings(
      withinCategoryDuplicate,
      { issuer: ISSUER },
      NOW,
    ),
    /artifact URLs must be unique/,
  );
});

test('rejects malformed and ambiguous category or document labels', async () => {
  await assert.rejects(
    () => discoveryFor(fixtureHtml({
      earningsLabel: 'Earnings Results',
    }))({ issuer: ISSUER }),
    /category label is malformed/,
  );
  await assert.rejects(
    () => discoveryFor(fixtureHtml({
      earningsClasses: 'name__cell earnings-release conference-call',
    }))({ issuer: ISSUER }),
    /row category is ambiguous/,
  );

  const malformedMonth = rawSnapshot();
  malformedMonth.rows[0].groups[0].entries[0].label = 'Q1';
  assert.throws(
    () => normalizeCompanyFilings(malformedMonth, { issuer: ISSUER }, NOW),
    /must be a calendar month/,
  );
});

test('rejects issuer/category path mismatches, redirects, and invalid clocks', async () => {
  await assert.rejects(
    () => discoveryFor(fixtureHtml({
      earningsJanuaryUrl: (
        'https://files.tijorifinance.com/insight/india/999/'
        + 'Earnings%20Release/ER-Jan26.pdf'
      ),
    }))({ issuer: ISSUER }),
    /issuer\/category bound/,
  );

  await assert.rejects(
    () => discoveryFor(fixtureHtml(), {
      responseUrl: 'https://www.tijorifinance.com/dashboard/',
    })({ issuer: ISSUER }),
    /did not match the resolved issuer/,
  );

  const invalidClock = createCompanyFilingsDiscovery({
    browserRunner: browserRunnerFor(fixtureHtml()),
    clock: () => new Date('invalid'),
  });
  await assert.rejects(
    () => invalidClock({ issuer: ISSUER }),
    /returned an invalid date/,
  );
});
