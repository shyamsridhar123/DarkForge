/**
 * TypeScript types for the OpenSandbox Azure JS SDK.
 * Mirrors the Python Pydantic models exactly.
 */

export type SessionState =
  | 'pending'
  | 'running'
  | 'terminating'
  | 'terminated'
  | 'error';

export type IdentityTier = 'user_bound' | 'shared_warm_pool';

export interface Session {
  session_id: string;
  image: string;
  state: SessionState;
  identity_tier: IdentityTier;
  created_at: string; // ISO-8601
  node_name?: string | null;
  low_latency: boolean;
}

export interface RunResult {
  session_id: string;
  command: string;
  stdout: string;
  stderr: string;
  exit_code: number;
  duration_ms: number;
  trace_id?: string | null;
}

export interface CreateSessionOptions {
  /** Fully-qualified container image reference. */
  image: string;
  /**
   * If true, request a pod from the shared warm-pool tier.
   * Requires the SandboxLowLatency Entra role assignment.
   */
  low_latency?: boolean;
  /** Environment variables to inject into the sandbox. */
  env?: Record<string, string>;
}

export interface SandboxClientOptions {
  /** Base URL of the control-plane API. No trailing slash. */
  apiUrl: string;
  /**
   * azure-identity TokenCredential.
   * Defaults to DefaultAzureCredential.
   * Do NOT pass raw tokens — always use azure-identity.
   */
  credential?: import('@azure/identity').TokenCredential;
  /**
   * OAuth2 scope. Must be api://<api-app-id>/.default.
   * Required — no default because the App ID is deployment-specific.
   */
  scope: string;
  /** Default HTTP timeout in milliseconds. Default: 30_000. */
  timeoutMs?: number;
}
