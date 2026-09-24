---
poc_id: poc_01K5ZGF1XTVREG0000000000A1
spec_version: v001
title: Kirana Basket — live "also bought" recommendations
stack: {backend: node-express, frontend: react-vite, database: mongodb}
user_stories:
  - id: us-01
    title: Shopper sees "also bought" recommendations on a product page
    actor: shopper
    acceptance: ["at least 5 recommendations render for a product with order history", "recommendation API responds under 300 ms p95"]
    testids: [us-01-product-name, us-01-reco-list, us-01-reco-item]
  - id: us-02
    title: Shopper browses products by category with prices
    actor: shopper
    acceptance: ["category list renders", "selecting a category lists its products with price in INR"]
    testids: [us-02-category-list, us-02-category-item, us-02-product-list, us-02-product-item]
  - id: us-03
    title: Category manager sees top-selling products over the last 30 days
    actor: category manager
    acceptance: ["dashboard lists top 10 products by units sold", "counts match an aggregation over orders"]
    testids: [us-03-top-list, us-03-top-item]
success_criteria:
  - id: sc-01
    statement: GET /api/products/{sku}/recommendations p95 latency under 300 ms with 5 000 products and 8 000 orders
    automatable: true
  - id: sc-02
    statement: Every product with at least one order returns >= 5 recommendations
    automatable: true
  - id: sc-03
    statement: Top-products dashboard counts equal the orders aggregation
    automatable: true
  - id: sc-04
    statement: Founders are convinced the recommendations make sense
    automatable: false
seed_requirements: {max_docs_per_collection: 10000, realism: "Indian online grocery, en-IN locale, INR prices"}
assumptions:
  - "Synthetic data is acceptable; shapes follow the customer's Postgres/MySQL tables"
  - "No authentication in the POC"
  - "Co-purchase is computed from the last 90 days of orders"
approved: false
---

# Executive summary
Kirana Basket, an online grocery marketplace for tier-2 cities in Karnataka and Tamil Nadu, wants live "customers also bought" recommendations on every product page, served from order history in MongoDB instead of a four-hour nightly batch across Postgres and MySQL.

# Business objective
Increase basket size by turning a dead-end product page into a discovery surface, and prove that co-purchase recommendations can be served live from operational data.

# Users and user stories
- us-01 Shopper (mobile web): sees at least five "also bought" items on any product page.
- us-02 Shopper: browses the catalogue by category with INR prices.
- us-03 Category manager (desktop): sees the top-selling products over the last 30 days.

# Functional scope
In: product browsing by category, product page with recommendations, top-products dashboard.
Out: per-customer personalisation, search, checkout, authentication, catalogue migration.

# Data model summary
Two collections, `products` and `orders`; order line items embed the SKU, quantity and unit price. See schema_design.json.

# Key queries
- qp-01 co-purchase recommendations for a SKU (orders → unwind items → group by co-occurring SKU → sort → limit 10 → lookup products).
- qp-02 products by category, sorted by name.
- qp-03 top products by units sold in the last 30 days.
See query_patterns.json.

# API surface (high level)
GET /api/health · GET /api/categories · GET /api/products?category= · GET /api/products/{sku} · GET /api/products/{sku}/recommendations · GET /api/dashboard/top-products. The OpenAPI contract is authoritative.

# Non-functional requirements
Recommendation endpoint p95 < 300 ms on t3.medium with the seeded volume. Deployed in AWS ap-south-1. Single origin (nginx serves the frontend and proxies /api).

# Success criteria
sc-01 to sc-04 above; sc-04 is not automatable.

# Assumptions and open questions
Listed in the front matter. Open: whether the 90-day co-purchase window is what the business wants.

# Timeline and constraints
Two weeks to a founder demo. AWS Mumbai. Must run as a real web app.
