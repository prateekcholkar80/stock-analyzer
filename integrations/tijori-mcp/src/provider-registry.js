import { createCompanyOverviewHandler } from './company-overview.js';
import { createCompanyFilingDownloader } from './company-filing-download.js';
import { createCompanyFilingsDiscovery } from './company-filings.js';
import { createFinancialsHandler } from './financials.js';
import { failureResult, successResult } from './result-envelope.js';
import { createResolveCompanyIdsHandler } from './resolve-company-ids.js';
import { createSearchCompanyHandler } from './search-company.js';
import { createShareholdingHandler } from './shareholding.js';
import { createToolRegistry } from './tool-registry.js';


export function createTijoriProviderRegistry({
  browserRunner,
  filingStagingDirectory = undefined,
  filingDownloadFetcher = globalThis.fetch,
  filingDownloadClock = () => new Date(),
}) {
  const discoverCompanyFilings = createCompanyFilingsDiscovery({ browserRunner });
  const downloadCompanyFiling = filingStagingDirectory === undefined
    ? undefined
    : createCompanyFilingDownloader({
      browserRunner,
      stagingDirectory: filingStagingDirectory,
      fetcher: filingDownloadFetcher,
      clock: filingDownloadClock,
    });
  return createToolRegistry({
    search_company: createSearchCompanyHandler({ browserRunner }),
    resolve_company_ids: createResolveCompanyIdsHandler({ browserRunner }),
    get_company_overview: createCompanyOverviewHandler({ browserRunner }),
    get_financials: createFinancialsHandler({ browserRunner }),
    get_shareholding: createShareholdingHandler({ browserRunner }),
    list_company_filings: async (argumentsValue) => {
      try {
        return successResult(
          'list_company_filings',
          await discoverCompanyFilings(argumentsValue),
        );
      } catch {
        return failureResult('list_company_filings', 'unavailable');
      }
    },
    download_company_filing: async (argumentsValue) => {
      if (downloadCompanyFiling === undefined) {
        return failureResult('download_company_filing', 'unavailable');
      }
      try {
        return successResult(
          'download_company_filing',
          await downloadCompanyFiling(argumentsValue),
        );
      } catch {
        return failureResult('download_company_filing', 'unavailable');
      }
    },
  });
}
