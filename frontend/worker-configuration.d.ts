interface Fetcher {
  fetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response>;
}

interface D1Database {
  readonly __d1Brand?: never;
}

declare module "cloudflare:workers" {
  const env: { DB: D1Database };
  export { env };
}
