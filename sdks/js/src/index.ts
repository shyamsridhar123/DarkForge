/**
 * @opensandbox/azure — Public API surface.
 */

export { SandboxClient, SessionHandle } from './client';
export type { Session, RunResult, CreateSessionOptions, SandboxClientOptions, SessionState, IdentityTier } from './models';
export {
  SandboxError,
  AuthenticationError,
  AuthorizationError,
  RateLimitError,
  SessionNotFoundError,
  PropagationTimeoutError,
} from './errors';
