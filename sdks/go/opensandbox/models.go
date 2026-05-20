package opensandbox

import "time"

// SessionState represents the lifecycle state of a sandbox session.
type SessionState string

const (
	SessionStatePending     SessionState = "pending"
	SessionStateRunning     SessionState = "running"
	SessionStateTerminating SessionState = "terminating"
	SessionStateTerminated  SessionState = "terminated"
	SessionStateError       SessionState = "error"
)

// IdentityTier describes the identity isolation tier for a session.
type IdentityTier string

const (
	// IdentityTierUserBound — pod runs with a per-user UAMI (Workload Identity FC).
	// Every action traces to the calling user's Entra OID.
	IdentityTierUserBound IdentityTier = "user_bound"

	// IdentityTierSharedWarmPool — pod taken from a pre-warmed shared pool.
	// Lower cold-start latency; requires the SandboxLowLatency Entra role.
	IdentityTierSharedWarmPool IdentityTier = "shared_warm_pool"
)

// Session represents a running sandbox session returned by the server.
type Session struct {
	SessionID    string       `json:"session_id"`
	Image        string       `json:"image"`
	State        SessionState `json:"state"`
	IdentityTier IdentityTier `json:"identity_tier"`
	CreatedAt    time.Time    `json:"created_at"`
	NodeName     *string      `json:"node_name,omitempty"`
	LowLatency   bool         `json:"low_latency"`
}

// RunResult is the result of a command executed inside a sandbox session.
type RunResult struct {
	SessionID  string  `json:"session_id"`
	Command    string  `json:"command"`
	Stdout     string  `json:"stdout"`
	Stderr     string  `json:"stderr"`
	ExitCode   int     `json:"exit_code"`
	DurationMs int     `json:"duration_ms"`
	TraceID    *string `json:"trace_id,omitempty"`
}

// CreateSessionRequest is the payload for CreateSession.
type CreateSessionRequest struct {
	// Image is the fully-qualified container image reference.
	Image string `json:"image"`

	// LowLatency requests a pod from the shared warm-pool tier.
	// Requires the SandboxLowLatency Entra role assignment.
	LowLatency bool `json:"low_latency,omitempty"`

	// Env is optional environment variables to inject into the sandbox.
	Env map[string]string `json:"env,omitempty"`
}
