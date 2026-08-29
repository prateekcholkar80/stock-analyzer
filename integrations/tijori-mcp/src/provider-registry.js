import { createCompanyOverviewHandler } from './company-overview.js';
import { createFinancialsHandler } from './financials.js';
import { createResolveCompanyIdsHandler } from './resolve-company-ids.js';
import { createSearchCompanyHandler } from './search-company.js';
import { createShareholdingHandler } from './shareholding.js';
import { createToolRegistry } from './tool-registry.js';


export function createTijoriProviderRegistry({ browserRunner }) {
  return createToolRegistry({
    search_company: createSearchCompanyHandler({ browserRunner }),
    resolve_company_ids: createResolveCompanyIdsHandler({ browserRunner }),
    get_company_overview: createCompanyOverviewHandler({ browserRunner }),
    get_financials: createFinancialsHandler({ browserRunner }),
    get_shareholding: createShareholdingHandler({ browserRunner }),
  });
}
