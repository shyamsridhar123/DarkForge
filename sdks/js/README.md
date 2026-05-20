# @opensandbox/azure

> **Pre-release stub.** The API surface is complete; business logic is not yet implemented. See TODO markers in `src/client.ts`.

TypeScript/JavaScript SDK for [OpenSandbox on Azure](https://github.com/your-org/opensandbox).

## Install

```bash
npm install @opensandbox/azure @azure/identity
```

## Planned usage

```typescript
import { SandboxClient } from '@opensandbox/azure';
import { DefaultAzureCredential } from '@azure/identity';

const client = new SandboxClient({
  apiUrl: 'https://api-opensandbox.example.com',
  credential: new DefaultAzureCredential(),
  scope: 'api://<api-app-id>/.default',
});

const sess = await client.createSession({
  image: 'acr.example.azurecr.io/sandbox/base/python:3.12',
});
try {
  const result = await sess.run("python -c 'print(\"hello\")'");
  console.log(result.stdout);
} finally {
  await sess.delete();
}
```

## Authentication

Uses [`@azure/identity`](https://www.npmjs.com/package/@azure/identity) exclusively — no hand-rolled MSAL. Pass any `TokenCredential` implementation. `DefaultAzureCredential` is used when no credential is supplied.

## Status

| Method | Status |
|---|---|
| `createSession` | TODO |
| `listSessions` | TODO |
| `getSession` | TODO |
| `SessionHandle.run` | TODO |
| `SessionHandle.delete` | TODO |

## Development

```bash
npm install
npm run build
npm test
```
