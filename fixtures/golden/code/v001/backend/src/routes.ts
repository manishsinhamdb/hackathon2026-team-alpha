import { Router, Request, Response, NextFunction } from "express";
import mongoose from "mongoose";
import { ProductModel, OrderModel } from "./models.js";

export const api = Router();
const PROJECT = { _id: 0, sku: 1, name: 1, category: 1, brand: 1, price_inr: 1 };
const daysAgo = (d: number) => new Date(Date.now() - d * 86400000);
const clamp = (v: unknown, lo: number, hi: number, dflt: number) => { const n = parseInt(String(v ?? ""), 10); return Number.isFinite(n) ? Math.max(lo, Math.min(hi, n)) : dflt; };
const wrap = (fn: (req: Request, res: Response) => Promise<unknown>) => (req: Request, res: Response, next: NextFunction) => fn(req, res).catch(next);

// GET /api/health — 200 only when a DB round-trip succeeds (§6.5)
api.get("/health", wrap(async (_req, res) => {
  try {
    await mongoose.connection.db!.admin().ping();
    res.json({ status: "ok", db: "connected" });
  } catch {
    res.status(503).json({ error: { code: "DB_UNAVAILABLE", message: "database ping failed" } });
  }
}));

// GET /api/categories — listCategories (us-02)
api.get("/categories", wrap(async (_req, res) => {
  const items = await ProductModel.aggregate([{ $group: { _id: "$category", count: { $sum: 1 } } }, { $sort: { _id: 1 } },
    { $project: { _id: 0, name: "$_id", count: 1 } }]);
  res.json({ items });
}));

// GET /api/products?category=&limit=&cursor= — listProducts (us-02, qp-02)
api.get("/products", wrap(async (req, res) => {
  const limit = clamp(req.query.limit, 1, 100, 50);
  const q: Record<string, unknown> = {};
  if (req.query.category) q.category = String(req.query.category);
  if (req.query.cursor) q.name = { $gt: Buffer.from(String(req.query.cursor), "base64url").toString() };
  const docs = await ProductModel.find(q, PROJECT).sort({ name: 1, sku: 1 }).limit(limit + 1).lean();
  const items = docs.slice(0, limit);
  const next_cursor = docs.length > limit ? Buffer.from(items[items.length - 1].name).toString("base64url") : null;
  res.json({ items, next_cursor });
}));

// GET /api/products/:sku — getProduct (us-01)
api.get("/products/:sku", wrap(async (req, res) => {
  const p = await ProductModel.findOne({ sku: req.params.sku }, PROJECT).lean();
  if (!p) return res.status(404).json({ error: { code: "NOT_FOUND", message: `unknown sku ${req.params.sku}` } });
  res.json(p);
}));

// GET /api/products/:sku/recommendations — getRecommendations (us-01, qp-01)
api.get("/products/:sku/recommendations", wrap(async (req, res) => {
  const sku = req.params.sku;
  const limit = clamp(req.query.limit, 1, 20, 10);
  if (!(await ProductModel.exists({ sku }))) return res.status(404).json({ error: { code: "NOT_FOUND", message: `unknown sku ${sku}` } });
  const items = await OrderModel.aggregate([
    { $match: { "items.sku": sku, ordered_at: { $gte: daysAgo(90) } } },
    { $unwind: "$items" },
    { $match: { "items.sku": { $ne: sku } } },
    { $group: { _id: "$items.sku", orders_together: { $sum: 1 } } },
    { $sort: { orders_together: -1, _id: 1 } },
    { $limit: limit },
    { $lookup: { from: "products", localField: "_id", foreignField: "sku", as: "product" } },
    { $unwind: "$product" },
    { $project: { _id: 0, orders_together: 1, sku: "$product.sku", name: "$product.name", category: "$product.category", brand: "$product.brand", price_inr: "$product.price_inr" } },
  ]);
  res.json({ sku, items });
}));

// GET /api/dashboard/top-products?days=&limit= — getTopProducts (us-03, qp-03)
api.get("/dashboard/top-products", wrap(async (req, res) => {
  const days = clamp(req.query.days, 1, 365, 30);
  const limit = clamp(req.query.limit, 1, 50, 10);
  const items = await OrderModel.aggregate([
    { $match: { ordered_at: { $gte: daysAgo(days) } } },
    { $unwind: "$items" },
    { $group: { _id: "$items.sku", units: { $sum: "$items.qty" } } },
    { $sort: { units: -1, _id: 1 } },
    { $limit: limit },
    { $lookup: { from: "products", localField: "_id", foreignField: "sku", as: "product" } },
    { $unwind: "$product" },
    { $project: { _id: 0, units: 1, sku: "$product.sku", name: "$product.name", category: "$product.category", brand: "$product.brand", price_inr: "$product.price_inr" } },
  ]);
  res.json({ days, items });
}));
