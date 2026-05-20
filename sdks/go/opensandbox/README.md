# opensandbox-azure-go

> **Pre-release stub.** The API surface is complete; business logic is NOT implemented. Every method returns `errors.New("not implemented")`. See TODO comments in `client.go`.

Go SDK for [OpenSandbox on Azure](https://github.com/your-org/opensandbox).

## Install

```bash
go get github.com/your-org/opensandbox-azure-go
```

## Planned usage

```go
package main

import (
    "context"
    "fmt"
    "log"

    opensandbox "github.com/your-org/opensandbox-azure-go"
    "github.com/Azure/azure-sdk-for-go/sdk/azidentity"
)

func main() {
    cred, err := azidentity.NewDefaultAzureCredential(nil)
    if err != nil {
        log.Fatal(err)
    }

    client, err := opensandbox.NewClient(opensandbox.ClientOptions{
        APIURL:     "https://api-opensandbox.example.com",
        Credential: cred,
        Scope:      "api://<api-app-id>/.default",
    })
    if err != nil {
        log.Fatal(err)
    }

    ctx := context.Background()
    sess, err := client.CreateSession(ctx, opensandbox.CreateSessionRequest{
        Image: "acr.example.azurecr.io/sandbox/base/python:3.12",
    })
    if err != nil {
        log.Fatal(err)
    }
    defer sess.Delete(ctx)

    result, err := sess.Run(ctx, "python -c 'print(\"hello\")'", 0)
    if err != nil {
        log.Fatal(err)
    }
    fmt.Println(result.Stdout)
}
```

## Authentication

Uses [`azidentity`](https://pkg.go.dev/github.com/Azure/azure-sdk-for-go/sdk/azidentity) exclusively — no hand-rolled MSAL. Pass any `azcore.TokenCredential`. `DefaultAzureCredential` is used when `Credential` is nil.

## Error handling

```go
import "errors"

_, err := client.CreateSession(ctx, req)
if errors.Is(err, opensandbox.ErrAuthentication) {
    // 401
} else if errors.Is(err, opensandbox.ErrPropagationTimeout) {
    var pe *opensandbox.PropagationTimeoutError
    errors.As(err, &pe)
    fmt.Printf("retry after %ds\n", pe.RetryAfter)
}
```

## Status

| Method | Status |
|---|---|
| `NewClient` | ✅ implemented |
| `CreateSession` | TODO |
| `ListSessions` | TODO |
| `GetSession` | TODO |
| `SessionHandle.Run` | TODO |
| `SessionHandle.Delete` | TODO |

## Development

```bash
go test ./...
```
