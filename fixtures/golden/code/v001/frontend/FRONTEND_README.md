# Golden frontend
Vite + React 18 + TypeScript. `src/api/client.ts` mirrors `../api_contract.yaml` one function per operationId. Reads `VITE_API_BASE_URL` at build time (default `/api`). One route per user story: `/categories` (us-02), `/products/:sku` (us-01), `/dashboard` (us-03), each carrying the `data-testid`s declared in `poc_spec.md`. Builds to static `dist/` for nginx.
