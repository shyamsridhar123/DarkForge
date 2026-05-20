/**
 * Tests for SandboxClient (stub).
 *
 * Only the constructor assertion is implemented.
 * Each un-implemented method has a TODO comment marking where the real test goes.
 */

import { SandboxClient } from '../src/client';
import { SandboxClientOptions } from '../src/models';

const MOCK_CREDENTIAL = {
  getToken: jest.fn().mockResolvedValue({ token: 'fake-token', expiresOnTimestamp: Date.now() + 3600_000 }),
};

const BASE_OPTIONS: SandboxClientOptions = {
  apiUrl: 'https://api-opensandbox.example.com',
  credential: MOCK_CREDENTIAL as any,
  scope: 'api://my-app-id/.default',
};

// ---------------------------------------------------------------------------
// Constructor
// ---------------------------------------------------------------------------

describe('SandboxClient constructor', () => {
  it('creates a non-null client with valid options', () => {
    const client = new SandboxClient(BASE_OPTIONS);
    expect(client).toBeDefined();
    expect(client).toBeInstanceOf(SandboxClient);
  });

  it('throws when scope is missing', () => {
    expect(
      () => new SandboxClient({ ...BASE_OPTIONS, scope: '' }),
    ).toThrow('scope is required');
  });
});

// ---------------------------------------------------------------------------
// createSession
// ---------------------------------------------------------------------------

describe('SandboxClient.createSession', () => {
  // TODO: mock axios with nock, assert:
  //   - POST /sessions is called
  //   - Authorization: Bearer <token> header is present
  //   - traceparent header matches W3C format 00-<32hex>-<16hex>-<2hex>
  //   - credential.getToken is called with BASE_OPTIONS.scope
  //   - 401 → AuthenticationError
  //   - 403 → AuthorizationError
  //   - 429 → RateLimitError with retryAfter
  //   - 503 → PropagationTimeoutError with retryAfter
  it.todo('sends Authorization header');
  it.todo('calls credential.getToken with correct scope');
  it.todo('attaches W3C traceparent header');
  it.todo('401 → AuthenticationError');
  it.todo('403 → AuthorizationError');
  it.todo('429 → RateLimitError');
  it.todo('503 → PropagationTimeoutError');
});

// ---------------------------------------------------------------------------
// listSessions
// ---------------------------------------------------------------------------

describe('SandboxClient.listSessions', () => {
  // TODO: mock GET /sessions, assert array of SessionHandle returned
  it.todo('returns array of SessionHandle');
});

// ---------------------------------------------------------------------------
// getSession
// ---------------------------------------------------------------------------

describe('SandboxClient.getSession', () => {
  // TODO: mock GET /sessions/:id
  //   - happy path returns SessionHandle
  //   - 404 → SessionNotFoundError
  it.todo('returns SessionHandle for known session');
  it.todo('404 → SessionNotFoundError');
});

// ---------------------------------------------------------------------------
// SessionHandle.run
// ---------------------------------------------------------------------------

describe('SessionHandle.run', () => {
  // TODO: mock POST /sessions/:id/run
  //   - returns RunResult with stdout/stderr/exit_code
  it.todo('returns RunResult');
});

// ---------------------------------------------------------------------------
// SessionHandle.delete
// ---------------------------------------------------------------------------

describe('SessionHandle.delete', () => {
  // TODO: mock DELETE /sessions/:id
  it.todo('calls DELETE /sessions/:id');
});
