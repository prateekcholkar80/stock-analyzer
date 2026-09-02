import process from 'node:process';
import { pathToFileURL } from 'node:url';

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import * as z from 'zod/v4';

import { probeAuthentication } from './authentication-probe.js';
import { createBrowserSessionRunner } from './browser-session.js';
import { createTijoriProviderRegistry } from './provider-registry.js';
import { failureResult, serializeToolResult } from './result-envelope.js';
import { loadSessionBoundary } from './session-boundary.js';
import { createToolRegistry } from './tool-registry.js';


const SERVER_INFO = Object.freeze({
  name: 'jarvis-local-tijori-mcp',
  version: '0.1.0',
});

const locatorSchema = z.looseObject({
  exchange: z.string().optional().nullable(),
  symbol: z.string().optional().nullable(),
  legal_name: z.string().optional().nullable(),
  isin: z.string().optional().nullable(),
  provider_company_id: z.string().optional().nullable(),
  provider_slug: z.string().optional().nullable(),
});

const issuerSchema = z.looseObject({
  exchange: z.string(),
  symbol: z.string(),
  legal_name: z.string(),
  isin: z.string().optional().nullable(),
  provider_company_id: z.string().optional().nullable(),
  provider_slug: z.string().optional().nullable(),
});

const TOOL_INPUT_SCHEMAS = Object.freeze({
  search_company: z.looseObject({
    query: z.string(),
    exchanges: z.array(z.string()).optional(),
    max_results: z.number().int().optional(),
  }),
  resolve_company_ids: z.looseObject({
    locator: locatorSchema,
    max_candidates: z.number().int().optional(),
  }),
  get_company_overview: z.looseObject({
    issuer: issuerSchema,
    as_of_date: z.string().optional().nullable(),
    document_type: z.enum([
      'peer_comparison',
      'benchmarking_financials',
    ]).optional(),
  }),
  get_financials: z.looseObject({
    issuer: issuerSchema,
    as_of_date: z.string().optional().nullable(),
    statements: z.array(z.string()).optional(),
    period_types: z.array(z.string()).optional(),
    max_periods: z.number().int().optional(),
    document_type: z.enum([
      'growth_table',
      'balance_sheet',
      'profit_and_loss',
      'cash_flow',
      'ratios',
      'quarterly_results',
    ]).optional(),
    reporting_basis: z.enum([
      'consolidated',
      'standalone',
      'not_applicable',
    ]).optional(),
  }),
  get_shareholding: z.looseObject({
    issuer: issuerSchema,
    as_of_date: z.string().optional().nullable(),
    quarters: z.number().int().optional(),
    include_promoter_pledge: z.boolean().optional(),
  }),
});

const READ_ONLY_ANNOTATIONS = Object.freeze({
  readOnlyHint: true,
  destructiveHint: false,
  idempotentHint: true,
  openWorldHint: true,
});

const UNCONFIGURED_SESSION = Object.freeze({
  authenticated: false,
  providerContractVersion: 'tijori.unconfigured.v1',
});

export function createUnavailableRegistry() {
  const handlers = Object.fromEntries(
    Object.keys(TOOL_INPUT_SCHEMAS).map((toolName) => [
      toolName,
      async () => failureResult(toolName, 'unavailable'),
    ]),
  );
  return createToolRegistry(handlers);
}

export function createTijoriMcpServer(
  registry = createUnavailableRegistry(),
  sessionBoundary = UNCONFIGURED_SESSION,
) {
  const server = new McpServer(SERVER_INFO, {
    capabilities: {
      experimental: {
        jarvisTijori: {
          authenticated: sessionBoundary.authenticated,
          providerContractVersion: sessionBoundary.providerContractVersion,
        },
      },
    },
  });
  for (const definition of registry.listTools()) {
    server.registerTool(
      definition.name,
      {
        description: definition.description,
        inputSchema: TOOL_INPUT_SCHEMAS[definition.name],
        annotations: READ_ONLY_ANNOTATIONS,
      },
      async (argumentsValue) => toolResponse(registry, definition.name, argumentsValue),
    );
  }
  return server;
}

export async function createConfiguredTijoriMcpServer(
  registry = undefined,
  environment = process.env,
  {
    authenticationProbe = probeAuthentication,
    browserSessionFactory = createBrowserSessionRunner,
    providerRegistryFactory = createTijoriProviderRegistry,
  } = {},
) {
  const sessionBoundary = await loadSessionBoundary(environment);
  let browserRunner;
  let authentication = Object.freeze({
    authenticated: false,
    status: 'unavailable',
  });
  try {
    browserRunner = browserSessionFactory(sessionBoundary);
    authentication = normalizeAuthenticationProbe(
      await authenticationProbe(browserRunner),
    );
  } catch {
    // Startup remains inspectable but all tools stay gated.
  }
  let effectiveRegistry = registry;
  if (
    effectiveRegistry === undefined
    && browserRunner !== undefined
    && authentication.authenticated
  ) {
    try {
      effectiveRegistry = providerRegistryFactory({ browserRunner });
    } catch {
      // Provider composition failures expose no details and no callable tools.
    }
  }
  effectiveRegistry ??= createUnavailableRegistry();
  const gatedRegistry = authentication.authenticated
    ? effectiveRegistry
    : authenticationGatedRegistry(effectiveRegistry, authentication.status);
  return createTijoriMcpServer(gatedRegistry, {
    authenticated: authentication.authenticated,
    providerContractVersion: sessionBoundary.providerContractVersion,
  });
}

export async function startStdioServer(registry = undefined) {
  assertSupportedNodeVersion();
  const server = await createConfiguredTijoriMcpServer(registry);
  await server.connect(new StdioServerTransport());
  return server;
}

export function assertSupportedNodeVersion(version = process.versions.node) {
  const major = Number.parseInt(version.split('.')[0], 10);
  if (major !== 24) {
    throw new Error('Tijori MCP requires the pinned Node 24 runtime');
  }
}

async function toolResponse(registry, toolName, argumentsValue) {
  let result;
  try {
    result = await registry.invoke(toolName, argumentsValue);
  } catch {
    result = failureResult(toolName, 'unavailable');
  }
  return {
    content: [{ type: 'text', text: serializeToolResult(result) }],
    isError: result.status !== 'success',
  };
}

function authenticationGatedRegistry(registry, probeStatus) {
  const failureStatus = probeStatus === 'authentication_required'
    ? 'authentication_required'
    : 'unavailable';
  return Object.freeze({
    listTools() {
      return registry.listTools();
    },
    async invoke(toolName) {
      return failureResult(toolName, failureStatus);
    },
  });
}

function normalizeAuthenticationProbe(candidate) {
  if (
    candidate?.authenticated === true
    && candidate?.status === 'authenticated'
  ) {
    return Object.freeze({ authenticated: true, status: 'authenticated' });
  }
  if (
    candidate?.authenticated === false
    && candidate?.status === 'authentication_required'
  ) {
    return Object.freeze({
      authenticated: false,
      status: 'authentication_required',
    });
  }
  return Object.freeze({ authenticated: false, status: 'unavailable' });
}

function isDirectExecution() {
  if (!process.argv[1]) return false;
  return pathToFileURL(process.argv[1]).href === import.meta.url;
}

if (isDirectExecution()) {
  startStdioServer().catch(() => {
    process.stderr.write('Tijori MCP could not start safely.\n');
    process.exitCode = 1;
  });
}
