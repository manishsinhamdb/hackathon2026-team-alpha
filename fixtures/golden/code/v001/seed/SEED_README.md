# Golden seed
Deterministic (mulberry32, seed 42). Drops and recreates `products` (5 000) and `orders` (8 000, 2–8 items each, 60 basket clusters over the last 120 days), creates the indexes from `schema_design.json`, prints `{"products": n, "orders": n}` as the last stdout line. Reads `MONGODB_URI` (database name from the URI path) and `SEED_MAX_DOCS`.
