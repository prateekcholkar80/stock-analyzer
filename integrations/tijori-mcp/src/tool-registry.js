import {
  APPROVED_TOOL_NAMES,
  validateToolResult,
} from './result-envelope.js';
import { validateToolArguments } from './tool-inputs.js';


const TOOL_DESCRIPTIONS = Object.freeze({
  search_company: 'Find Tijori company candidates for a user-supplied query.',
  resolve_company_ids: 'Resolve a market identifier to Tijori company identifiers.',
  get_company_overview: 'Retrieve a company overview from the authenticated Tijori account.',
  get_financials: 'Retrieve requested company financial-statement evidence.',
  get_shareholding: 'Retrieve requested company shareholding evidence.',
});

export const TOOL_DEFINITIONS = Object.freeze(
  APPROVED_TOOL_NAMES.map((name) => Object.freeze({
    name,
    description: TOOL_DESCRIPTIONS[name],
  })),
);

export function createToolRegistry(handlers) {
  validateHandlers(handlers);
  const immutableHandlers = Object.freeze({ ...handlers });

  return Object.freeze({
    listTools() {
      return TOOL_DEFINITIONS;
    },

    async invoke(toolName, argumentsValue = {}) {
      if (!Object.hasOwn(immutableHandlers, toolName)) {
        throw new TypeError('Tijori MCP attempted to invoke an unapproved tool');
      }
      const validatedArguments = validateToolArguments(toolName, argumentsValue);
      const result = validateToolResult(
        await immutableHandlers[toolName](validatedArguments),
      );
      if (result.tool_name !== toolName) {
        throw new TypeError('Tijori MCP handler returned a mismatched tool result');
      }
      return result;
    },
  });
}

function validateHandlers(handlers) {
  if (!isPlainObject(handlers)) {
    throw new TypeError('Tijori MCP handlers must be a plain object');
  }

  const actualNames = Object.keys(handlers).sort();
  const approvedNames = [...APPROVED_TOOL_NAMES].sort();
  if (
    actualNames.length !== approvedNames.length
    || actualNames.some((name, index) => name !== approvedNames[index])
  ) {
    throw new TypeError('Tijori MCP handlers must match the exact tool allow-list');
  }
  for (const name of APPROVED_TOOL_NAMES) {
    if (typeof handlers[name] !== 'function') {
      throw new TypeError(`Tijori MCP handler is not callable: ${name}`);
    }
  }
}

function isPlainObject(value) {
  if (value === null || typeof value !== 'object') return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}
