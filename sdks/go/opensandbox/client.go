// Package opensandbox provides a Go client for the OpenSandbox Azure control-plane API.
//
// STUB: API surface is complete; business logic is NOT implemented.
// Every method returns errors.New("not implemented").
// Search for "TODO:" comments to find each implementation site.
//
// Identity: uses github.com/Azure/azure-sdk-for-go/sdk/azidentity exclusively.
// Do NOT replace with hand-rolled MSAL or raw HTTP token acquisition.
//
// Traceparent: every outbound request MUST carry a W3C traceparent header
// (Critic S-C9). See generateTraceparent() in this file.
package opensandbox

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/Azure/azure-sdk-for-go/sdk/azcore"
	"github.com/Azure/azure-sdk-for-go/sdk/azcore/policy"
	"github.com/Azure/azure-sdk-for-go/sdk/azidentity"
)

// DefaultTimeout is the default HTTP timeout for all requests.
const DefaultTimeout = 30 * time.Second

// DefaultRunTimeout is the default timeout for session run() calls.
const DefaultRunTimeout = 60 * time.Second

// ClientOptions configures the SandboxClient.
type ClientOptions struct {
	// APIURL is the base URL of the control-plane API (no trailing slash).
	APIURL string

	// Credential is an azure-identity TokenCredential.
	// Defaults to azidentity.NewDefaultAzureCredential().
	Credential azcore.TokenCredential

	// Scope is the OAuth2 scope: api://<api-app-id>/.default
	// Required — no default because the App ID is deployment-specific.
	Scope string

	// Timeout overrides the default HTTP timeout. Zero uses DefaultTimeout.
	Timeout time.Duration
}

// Client is the OpenSandbox Azure SDK client.
type Client struct {
	apiURL     string
	credential azcore.TokenCredential
	scope      string
	httpClient *http.Client
}

// NewClient creates a new Client.
// Returns an error if Scope is empty or the default credential cannot be created.
func NewClient(opts ClientOptions) (*Client, error) {
	if opts.Scope == "" {
		return nil, errors.New("opensandbox: Scope is required (api://<api-app-id>/.default)")
	}

	cred := opts.Credential
	if cred == nil {
		var err error
		cred, err = azidentity.NewDefaultAzureCredential(nil)
		if err != nil {
			return nil, fmt.Errorf("opensandbox: failed to create DefaultAzureCredential: %w", err)
		}
	}

	timeout := opts.Timeout
	if timeout == 0 {
		timeout = DefaultTimeout
	}

	return &Client{
		apiURL:     strings.TrimRight(opts.APIURL, "/"),
		credential: cred,
		scope:      opts.Scope,
		httpClient: &http.Client{Timeout: timeout},
	}, nil
}

// getToken acquires a Bearer token. azidentity handles caching.
//
// TODO: call c.credential.GetToken(ctx, policy.TokenRequestOptions{Scopes: []string{c.scope}})
// and return token.Token.
func (c *Client) getToken(ctx context.Context) (string, error) {
	// TODO: implement
	_ = ctx
	return "", errors.New("not implemented: getToken")
}

// authHeaders returns the Authorization and traceparent headers for one request.
//
// TODO: call getToken, generate a traceparent via generateTraceparent(), return both.
func (c *Client) authHeaders(ctx context.Context) (map[string]string, error) {
	// TODO: implement
	return nil, errors.New("not implemented: authHeaders")
}

// raiseForStatus maps an HTTP status code to a typed SandboxError.
//
// TODO: implement mapping:
//   401 → *AuthenticationError
//   403 → *AuthorizationError
//   404 → *SessionNotFoundError{SessionID: sessionID}
//   429 → *RateLimitError{RetryAfter: parseRetryAfter(resp)}
//   503 → *PropagationTimeoutError{RetryAfter: parseRetryAfter(resp)}
//   other → *SandboxError{StatusCode: status}
func raiseForStatus(statusCode int, body string, sessionID string) error {
	// TODO: implement
	return fmt.Errorf("not implemented: raiseForStatus status=%d", statusCode)
}

// CreateSession creates a new sandbox session.
//
// TODO:
//  1. Marshal CreateSessionRequest to JSON.
//  2. POST {apiURL}/sessions with authHeaders.
//  3. Map errors via raiseForStatus.
//  4. Unmarshal response body into Session and return a *SessionHandle.
func (c *Client) CreateSession(ctx context.Context, req CreateSessionRequest) (*SessionHandle, error) {
	// TODO: implement
	return nil, errors.New("not implemented: CreateSession")
}

// ListSessions returns all active sessions visible to the caller.
//
// TODO:
//  1. GET {apiURL}/sessions with authHeaders.
//  2. Map errors.
//  3. Unmarshal JSON array into []Session and return []*SessionHandle.
func (c *Client) ListSessions(ctx context.Context) ([]*SessionHandle, error) {
	// TODO: implement
	return nil, errors.New("not implemented: ListSessions")
}

// GetSession fetches a single session by ID.
//
// TODO:
//  1. GET {apiURL}/sessions/{sessionID} with authHeaders.
//  2. 404 → SessionNotFoundError.
//  3. Return *SessionHandle.
func (c *Client) GetSession(ctx context.Context, sessionID string) (*SessionHandle, error) {
	// TODO: implement
	return nil, errors.New("not implemented: GetSession")
}

// run executes a command inside a session. Called by SessionHandle.Run.
//
// TODO:
//  1. POST {apiURL}/sessions/{sessionID}/run with {"command": command}.
//  2. Use runTimeout for this specific request (override client default).
//  3. Return *RunResult.
func (c *Client) run(ctx context.Context, sessionID string, command string, timeout time.Duration) (*RunResult, error) {
	// TODO: implement
	return nil, errors.New("not implemented: run")
}

// deleteSession terminates a session. Called by SessionHandle.Delete.
//
// TODO: DELETE {apiURL}/sessions/{sessionID} with authHeaders.
func (c *Client) deleteSession(ctx context.Context, sessionID string) error {
	// TODO: implement
	return errors.New("not implemented: deleteSession")
}

// ---------------------------------------------------------------------------
// SessionHandle
// ---------------------------------------------------------------------------

// SessionHandle wraps a Session and exposes Run and Delete.
type SessionHandle struct {
	client  *Client
	Session Session
}

// Run executes command inside this session.
//
// TODO: delegate to c.client.run(ctx, c.Session.SessionID, command, timeout).
func (h *SessionHandle) Run(ctx context.Context, command string, timeout time.Duration) (*RunResult, error) {
	// TODO: implement
	return nil, errors.New("not implemented: SessionHandle.Run")
}

// Delete terminates and deletes this session.
//
// TODO: delegate to h.client.deleteSession(ctx, h.Session.SessionID).
func (h *SessionHandle) Delete(ctx context.Context) error {
	// TODO: implement
	return errors.New("not implemented: SessionHandle.Delete")
}

// ---------------------------------------------------------------------------
// W3C traceparent (Critic S-C9 — required for cold-path trace assertion)
// ---------------------------------------------------------------------------

// generateTraceparent returns a fresh W3C traceparent header value.
// Format: 00-<32 hex chars>-<16 hex chars>-01
//
// TODO: replace the math/rand placeholder with crypto/rand for production use.
func generateTraceparent() string {
	// TODO: use crypto/rand to generate 16 bytes (trace-id) and 8 bytes (parent-id)
	// then format as: fmt.Sprintf("00-%x-%x-01", traceID, parentID)
	return "00-00000000000000000000000000000000-0000000000000000-01" // placeholder
}
