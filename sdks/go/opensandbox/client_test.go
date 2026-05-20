package opensandbox

import (
	"testing"
)

// TestNewClient_NonNil asserts that NewClient returns a non-nil client
// when valid options are provided.
func TestNewClient_NonNil(t *testing.T) {
	client, err := NewClient(ClientOptions{
		APIURL: "https://api-opensandbox.example.com",
		// Credential left nil → DefaultAzureCredential is created.
		// In CI without Azure credentials this will still construct but
		// fail on first token request, which is acceptable for a stub test.
		Scope: "api://my-app-id/.default",
	})
	if err != nil {
		// DefaultAzureCredential construction should not fail even without
		// ambient credentials; log and skip rather than fail hard.
		t.Logf("NewClient returned error (possibly no ambient credential): %v", err)
		t.Skip("skipping: no Azure credential available in this environment")
	}
	if client == nil {
		t.Fatal("expected non-nil *Client, got nil")
	}
}

// TestNewClient_RequiresScope asserts that NewClient returns an error
// when Scope is empty.
func TestNewClient_RequiresScope(t *testing.T) {
	_, err := NewClient(ClientOptions{
		APIURL: "https://api-opensandbox.example.com",
		Scope:  "", // intentionally empty
	})
	if err == nil {
		t.Fatal("expected error for empty Scope, got nil")
	}
}

// TODO: TestCreateSession — mock net/http transport, assert:
//   - POST /sessions is called
//   - Authorization: Bearer <token> header is present
//   - traceparent header matches W3C format 00-<32hex>-<16hex>-<2hex>
//   - 401 → *AuthenticationError (errors.Is(err, ErrAuthentication))
//   - 403 → *AuthorizationError
//   - 429 → *RateLimitError with RetryAfter populated
//   - 503 → *PropagationTimeoutError with RetryAfter populated

// TODO: TestListSessions — mock GET /sessions

// TODO: TestGetSession — mock GET /sessions/:id
//   - happy path
//   - 404 → *SessionNotFoundError

// TODO: TestSessionHandle_Run — mock POST /sessions/:id/run

// TODO: TestSessionHandle_Delete — mock DELETE /sessions/:id
