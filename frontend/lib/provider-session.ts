export type ProviderSessionStatus =
  | "unconfigured"
  | "ready"
  | "expired"
  | "revoked";

export type ProviderSessionRevocationReason =
  | "user_requested"
  | "connection_removed"
  | "security_rotation"
  | "expired";

export type ProviderSessionTarget = {
  provider_connection_id: string;
  provider: string;
  account_reference_hash?: string | null;
};

export type ProviderSessionLifecycle = {
  schema_version: "jarvis.http_provider_session.v1";
  provider_connection_id: string;
  provider: string;
  status: ProviderSessionStatus;
  checked_at: string;
  provisioned_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  revocation_reason: ProviderSessionRevocationReason | null;
  lifecycle_fingerprint: string;
};

export type ProviderSessionClient = {
  status(target: ProviderSessionTarget): Promise<ProviderSessionLifecycle>;
  provision(
    target: ProviderSessionTarget,
    options: { idempotencyKey: string; replaceExisting?: boolean },
  ): Promise<ProviderSessionLifecycle>;
  revoke(
    target: ProviderSessionTarget,
    options: {
      idempotencyKey: string;
      reason?: ProviderSessionRevocationReason;
    },
  ): Promise<ProviderSessionLifecycle>;
};

type ProviderSessionFetch = (
  input: string,
  init: RequestInit,
) => Promise<Response>;

export class ProviderSessionClientError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ProviderSessionClientError";
    this.status = status;
  }
}

const STATUSES = new Set<ProviderSessionStatus>([
  "unconfigured",
  "ready",
  "expired",
  "revoked",
]);

const REVOCATION_REASONS = new Set<ProviderSessionRevocationReason>([
  "user_requested",
  "connection_removed",
  "security_rotation",
  "expired",
]);

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function parseLifecycle(value: unknown): ProviderSessionLifecycle {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ProviderSessionClientError(502, "Provider connection returned an invalid response.");
  }
  const result = value as Record<string, unknown>;
  const status = result.status;
  const revocationReason = result.revocation_reason;
  if (
    result.schema_version !== "jarvis.http_provider_session.v1"
    || typeof result.provider_connection_id !== "string"
    || typeof result.provider !== "string"
    || typeof status !== "string"
    || !STATUSES.has(status as ProviderSessionStatus)
    || typeof result.checked_at !== "string"
    || !isNullableString(result.provisioned_at)
    || !isNullableString(result.expires_at)
    || !isNullableString(result.revoked_at)
    || !(
      revocationReason === null
      || (typeof revocationReason === "string"
        && REVOCATION_REASONS.has(revocationReason as ProviderSessionRevocationReason))
    )
    || typeof result.lifecycle_fingerprint !== "string"
  ) {
    throw new ProviderSessionClientError(502, "Provider connection returned an invalid response.");
  }
  return result as ProviderSessionLifecycle;
}

function failureMessage(status: number): string {
  if (status === 401) return "The Jarvis browser session is no longer authorized.";
  if (status === 404) return "The provider connection is not configured for this session.";
  if (status === 422) return "The provider connection request was rejected as invalid.";
  return "The provider connection service is unavailable.";
}

export function createProviderSessionClient(options: {
  apiBase: string;
  sessionId: string;
  accessToken: string;
  fetcher?: ProviderSessionFetch;
}): ProviderSessionClient {
  const base = options.apiBase.replace(/\/$/, "");
  const sessionId = encodeURIComponent(options.sessionId);
  const fetcher = options.fetcher ?? fetch;

  async function command(
    action: "status" | "provision" | "revoke",
    body: Record<string, unknown>,
  ): Promise<ProviderSessionLifecycle> {
    let response: Response;
    try {
      response = await fetcher(
        `${base}/api/v1/sessions/${sessionId}/provider-session/${action}`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Jarvis-Session-Token": options.accessToken,
          },
          body: JSON.stringify(body),
        },
      );
    } catch {
      throw new ProviderSessionClientError(
        0,
        "The provider connection service could not be reached.",
      );
    }
    if (!response.ok) {
      throw new ProviderSessionClientError(response.status, failureMessage(response.status));
    }
    try {
      return parseLifecycle(await response.json());
    } catch (error) {
      if (error instanceof ProviderSessionClientError) throw error;
      throw new ProviderSessionClientError(502, "Provider connection returned an invalid response.");
    }
  }

  return {
    status(target) {
      return command("status", { target });
    },
    provision(target, commandOptions) {
      return command("provision", {
        idempotency_key: commandOptions.idempotencyKey,
        target,
        user_interaction_authorized: true,
        replace_existing: commandOptions.replaceExisting ?? false,
      });
    },
    revoke(target, commandOptions) {
      return command("revoke", {
        idempotency_key: commandOptions.idempotencyKey,
        target,
        reason: commandOptions.reason ?? "user_requested",
        secure_delete_required: true,
      });
    },
  };
}
