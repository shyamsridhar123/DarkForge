package opensandbox

import "fmt"

// SandboxError is the base error type for all OpenSandbox SDK errors.
// Use errors.As to unwrap to specific subtypes.
type SandboxError struct {
	StatusCode int
	Message    string
}

func (e *SandboxError) Error() string {
	return fmt.Sprintf("opensandbox: HTTP %d: %s", e.StatusCode, e.Message)
}

// AuthenticationError is returned when the server responds with 401.
// Cause: missing or invalid bearer token, or OBO exchange failure.
type AuthenticationError struct {
	SandboxError
}

// AuthorizationError is returned when the server responds with 403.
// Cause: valid identity but insufficient permissions (e.g. missing role).
type AuthorizationError struct {
	SandboxError
}

// SessionNotFoundError is returned when the server responds with 404 for a session.
type SessionNotFoundError struct {
	SandboxError
	SessionID string
}

func (e *SessionNotFoundError) Error() string {
	return fmt.Sprintf("opensandbox: session not found: %s", e.SessionID)
}

// RateLimitError is returned when the server responds with 429.
// RetryAfter is the value of the Retry-After header in seconds, or 0 if absent.
type RateLimitError struct {
	SandboxError
	RetryAfter int
}

// PropagationTimeoutError is returned when the server responds with 503.
// This indicates a workload-identity FC propagation race (AC #4 / Task 3.2).
// The client should back off and retry using the RetryAfter hint.
// RetryAfter is the value of the Retry-After header in seconds, or 0 if absent.
type PropagationTimeoutError struct {
	SandboxError
	RetryAfter int
}

// Sentinel values for errors.Is matching.
//
// Example:
//
//	if errors.Is(err, ErrAuthentication) { ... }
var (
	ErrAuthentication      = &AuthenticationError{}
	ErrAuthorization       = &AuthorizationError{}
	ErrSessionNotFound     = &SessionNotFoundError{}
	ErrRateLimit           = &RateLimitError{}
	ErrPropagationTimeout  = &PropagationTimeoutError{}
)

// Is implements errors.Is support for sentinel matching on type alone.
func (e *AuthenticationError) Is(target error) bool {
	_, ok := target.(*AuthenticationError)
	return ok
}

func (e *AuthorizationError) Is(target error) bool {
	_, ok := target.(*AuthorizationError)
	return ok
}

func (e *SessionNotFoundError) Is(target error) bool {
	_, ok := target.(*SessionNotFoundError)
	return ok
}

func (e *RateLimitError) Is(target error) bool {
	_, ok := target.(*RateLimitError)
	return ok
}

func (e *PropagationTimeoutError) Is(target error) bool {
	_, ok := target.(*PropagationTimeoutError)
	return ok
}
