# Golden backend
Express + Mongoose, TypeScript compiled to `dist/`. Listens on `PORT` (8080) under `/api`. Reads `MONGODB_URI` from env only.
Implements every operation in `../api_contract.yaml`: getHealth, listCategories, listProducts, getProduct, getRecommendations, getTopProducts. Errors are `{error:{code,message}}`. `/api/health` returns 200 only after a successful DB ping.
