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
func (c *Client) getToken(ctx context.Context) (string, error) {
	token, err := c.credential.GetToken(ctx, policy.TokenRequestOptions{Scopes: []string{c.scope}})
	if err != nil {
		return "", fmt.Errorf("opensandbox: failed to acquire token: %w", err)
	}
	return token.Token, nil
}

// authHeaders returns the Authorization and traceparent headers for one request.
func (c *Client) authHeaders(ctx context.Context) (map[string]string, error) {
	tok, err := c.getToken(ctx)
	if err != nil {
		return nil, err
	}
	return map[string]string{
		"Authorization": "Bearer " + tok,
		"traceparent":   generateTraceparent(),
	}, nil
}

// raiseForStatus maps an HTTP status code to a typed SandboxError.
func raiseForStatus(statusCode int, body string, sessionID string) error {
	switch statusCode {
	case 401:
		return &AuthenticationError{SandboxError{StatusCode: statusCode, Message: body}}
	case 403:
		return &AuthorizationError{SandboxError{StatusCode: statusCode, Message: body}}
	case 404:
		return &SessionNotFoundError{SandboxError: SandboxError{StatusCode: statusCode, Message: body}, SessionID: sessionID}
	case 429:
		return &RateLimitError{SandboxError: SandboxError{StatusCode: statusCode, Message: body}}
	case 503:
		return &PropagationTimeoutError{SandboxError: SandboxError{StatusCode: statusCode, Message: body}}
	default:
		return &SandboxError{StatusCode: statusCode, Message: body}
	}
}

// doRequest performs an authenticated HTTP request and returns the response body.
func (c *Client) doRequest(ctx context.Context, method, url string, bodyReader *strings.Reader) (int, string, error) {
	headers, err := c.authHeaders(ctx)
	if err != nil {
		return 0, "", err
	}
	var req *http.Request
	if bodyReader != nil {
		req, err = http.NewRequestWithContext(ctx, method, url, bodyReader)
	} else {
		req, err = http.NewRequestWithContext(ctx, method, url, nil)
	}
	if err != nil {
		return 0, "", fmt.Errorf("opensandbox: failed to build request: %w", err)
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	if bodyReader != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return 0, "", fmt.Errorf("opensandbox: HTTP request failed: %w", err)
	}
	defer resp.Body.Close()
	var buf strings.Builder
	buf.Grow(512)
	_, _ = fmt.Fprintf(&buf, "")
	b := make([]byte, 512)
	for {
		n, readErr := resp.Body.Read(b)
		if n > 0 {
			buf.Write(b[:n])
		}
		if readErr != nil {
			break
		}
	}
	return resp.StatusCode, buf.String(), nil
}

// CreateSession creates a new sandbox session.
func (c *Client) CreateSession(ctx context.Context, req CreateSessionRequest) (*SessionHandle, error) {
	return nil, errors.New("not implemented in v1 scaffold")
}

// ListSessions returns all active sessions visible to the caller.
func (c *Client) ListSessions(ctx context.Context) ([]*SessionHandle, error) {
	return nil, errors.New("not implemented in v1 scaffold")
}

// GetSession fetches a single session by ID.
func (c *Client) GetSession(ctx context.Context, sessionID string) (*SessionHandle, error) {
	return nil, errors.New("not implemented in v1 scaffold")
}

// run executes a command inside a session. Called by SessionHandle.Run.
func (c *Client) run(ctx context.Context, sessionID string, command string, timeout time.Duration) (*RunResult, error) {
	return nil, errors.New("not implemented in v1 scaffold")
}

// deleteSession terminates a session. Called by SessionHandle.Delete.
func (c *Client) deleteSession(ctx context.Context, sessionID string) error {
	return errors.New("not implemented in v1 scaffold")
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
func (h *SessionHandle) Run(ctx context.Context, command string, timeout time.Duration) (*RunResult, error) {
	return h.client.run(ctx, h.Session.SessionID, command, timeout)
}

// Delete terminates and deletes this session.
func (h *SessionHandle) Delete(ctx context.Context) error {
	return h.client.deleteSession(ctx, h.Session.SessionID)
}

// ---------------------------------------------------------------------------
// W3C traceparent (Critic S-C9 — required for cold-path trace assertion)
// ---------------------------------------------------------------------------

// generateTraceparent returns a fresh W3C traceparent header value.
// Format: 00-<32 hex chars>-<16 hex chars>-01
func generateTraceparent() string {
	return fmt.Sprintf("00-%032x-%016x-01", time.Now().UnixNano(), time.Now().UnixMicro())
}
