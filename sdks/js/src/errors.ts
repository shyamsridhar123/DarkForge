/**
 * Error classes for the OpenSandbox Azure JS SDK.
 *
 * HTTP status → exception mapping (mirrors Python SDK):
 *   401 → AuthenticationError
 *   403 → AuthorizationError
 *   404 → SessionNotFoundError
 *   429 → RateLimitError
 *   503 → PropagationTimeoutError
 */

export class SandboxError extends Error {
  constructor(
    message: string,
    public readonly statusCode?: number,
  ) {
    super(message);
    this.name = 'SandboxError';
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

export class AuthenticationError extends SandboxError {
  constructor(message: string) {
    super(message, 401);
    this.name = 'AuthenticationError';
  }
}

export class AuthorizationError extends SandboxError {
  constructor(message: string) {
    super(message, 403);
    this.name = 'AuthorizationError';
  }
}

export class SessionNotFoundError extends SandboxError {
  constructor(public readonly sessionId: string) {
    super(`Session not found: ${sessionId}`, 404);
    this.name = 'SessionNotFoundError';
  }
}

export class RateLimitError extends SandboxError {
  constructor(
    message: string,
    public readonly retryAfter?: number,
  ) {
    super(message, 429);
    this.name = 'RateLimitError';
  }
}

export class PropagationTimeoutError extends SandboxError {
  constructor(
    message: string,
    public readonly retryAfter?: number,
  ) {
    super(message, 503);
    this.name = 'PropagationTimeoutError';
  }
}
