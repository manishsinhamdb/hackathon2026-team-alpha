// Golden seed script — deterministic (seed 42), idempotent (drop & recreate), respects SEED_MAX_DOCS.
// Prints a single JSON summary line on completion; exits non-zero on failure. Reads MONGODB_URI from env.
import { MongoClient } from "mongodb";

const uri = process.env.MONGODB_URI;
if (!uri) { console.error("MONGODB_URI is required"); process.exit(2); }
const MAX = Math.max(1, Math.min(parseInt(process.env.SEED_MAX_DOCS || "10000", 10), 100000));
const N_PRODUCTS = Math.min(5000, MAX);
const N_ORDERS = Math.min(8000, MAX);

// mulberry32 PRNG — same numbers every run
let s = 42 >>> 0;
const rand = () => { s = (s + 0x6D2B79F5) >>> 0; let t = s; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
const pick = (a) => a[Math.floor(rand() * a.length)];
const int = (lo, hi) => lo + Math.floor(rand() * (hi - lo + 1));

const CATEGORIES = {
  "Staples": ["Atta", "Rice", "Toor Dal", "Moong Dal", "Sugar", "Poha", "Rava", "Maida"],
  "Oils & Ghee": ["Sunflower Oil", "Groundnut Oil", "Coconut Oil", "Ghee", "Mustard Oil"],
  "Spices": ["Turmeric Powder", "Chilli Powder", "Garam Masala", "Sambar Powder", "Rasam Powder", "Cumin Seeds", "Mustard Seeds"],
  "Dairy": ["Milk 1L", "Curd 500g", "Paneer 200g", "Butter 100g", "Cheese Slices"],
  "Snacks": ["Murukku", "Mixture", "Banana Chips", "Namkeen", "Biscuits", "Khakhra"],
  "Beverages": ["Filter Coffee Powder", "Tea Powder", "Tender Coconut Water", "Buttermilk", "Fruit Juice"],
  "Fruits & Vegetables": ["Onion 1kg", "Tomato 1kg", "Potato 1kg", "Banana Dozen", "Coconut", "Curry Leaves", "Coriander"],
  "Personal Care": ["Soap", "Shampoo", "Toothpaste", "Hair Oil", "Face Wash"],
  "Household": ["Detergent", "Dishwash Bar", "Floor Cleaner", "Phenyl", "Garbage Bags"],
  "Bakery": ["Bread", "Buns", "Rusk", "Cake Slice"],
  "Frozen & Ready": ["Idli Batter", "Dosa Batter", "Frozen Peas", "Ready Chapati"],
  "Baby & Health": ["Diapers", "Baby Wipes", "ORS", "Vitamin C"],
};
const BRANDS = ["Aashirvaad", "Fortune", "MTR", "Nandini", "Aachi", "Haldiram's", "Amul", "Tata", "Nilgiris", "iD", "Patanjali", "24 Mantra", "Sakthi", "Anil", "Dabur"];
const SIZES = ["", " 250g", " 500g", " 1kg", " 2kg", " 5kg", " Pack of 2", " Family Pack"];

function products() {
  const out = [];
  const cats = Object.keys(CATEGORIES);
  for (let i = 0; i < N_PRODUCTS; i++) {
    const category = cats[i % cats.length];
    const base = pick(CATEGORIES[category]);
    out.push({ sku: `SKU-${100001 + i}`, name: `${pick(BRANDS)} ${base}${pick(SIZES)}`.trim(), category, brand: BRANDS[int(0, BRANDS.length - 1)],
      price_inr: int(10, 2000), created_at: new Date(Date.UTC(2026, 0, 1 + (i % 200))) });
  }
  return out;
}

function orders(prods) {
  // 60 "basket clusters" so co-purchase is non-trivial; every order draws 2–8 items, most from one cluster
  const clusters = Array.from({ length: 60 }, () => Array.from({ length: int(6, 12) }, () => pick(prods)));
  const out = [];
  const now = Date.now();
  for (let i = 0; i < N_ORDERS; i++) {
    const cluster = pick(clusters);
    const n = int(2, 8);
    const seen = new Set(); const items = [];
    while (items.length < n) {
      const p = rand() < 0.8 ? pick(cluster) : pick(prods);
      if (seen.has(p.sku)) continue;
      seen.add(p.sku);
      items.push({ sku: p.sku, qty: int(1, 4), unit_price_inr: p.price_inr });
    }
    out.push({ order_id: `ORD-${500001 + i}`, customer_id: `CUST-${int(1000, 6000)}`,
      ordered_at: new Date(now - int(0, 120) * 86400000 - int(0, 86399) * 1000), items,
      total_inr: items.reduce((t, it) => t + it.qty * it.unit_price_inr, 0) });
  }
  return out;
}

const client = new MongoClient(uri);
try {
  await client.connect();
  const db = client.db(); // database name comes from the URI path
  await db.collection("products").drop().catch(() => {});
  await db.collection("orders").drop().catch(() => {});
  const prods = products();
  await db.collection("products").insertMany(prods, { ordered: false });
  await db.collection("products").createIndexes([{ key: { sku: 1 }, unique: true }, { key: { category: 1, name: 1 } }]);
  const ords = orders(prods);
  for (let i = 0; i < ords.length; i += 1000) await db.collection("orders").insertMany(ords.slice(i, i + 1000), { ordered: false });
  await db.collection("orders").createIndexes([{ key: { order_id: 1 }, unique: true }, { key: { "items.sku": 1, ordered_at: -1 } }, { key: { ordered_at: -1 } }]);
  const summary = { products: await db.collection("products").countDocuments(), orders: await db.collection("orders").countDocuments() };
  console.log(JSON.stringify(summary));
} catch (e) {
  console.error("seed failed:", e.message);
  process.exit(1);
} finally {
  await client.close();
}
