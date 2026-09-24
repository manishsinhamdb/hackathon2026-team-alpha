// us-02: Shopper browses products by category with prices
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listCategories, listProducts, Category, Product } from "../api/client";

const inr = (n: number) => `₹${n.toLocaleString("en-IN")}`;

export function CategoriesPage() {
  const [cats, setCats] = useState<Category[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [products, setProducts] = useState<Product[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { listCategories().then((r) => { setCats(r.items); if (r.items[0]) setSelected(r.items[0].name); }).catch((e) => setErr(e.message)); }, []);
  useEffect(() => { if (!selected) return; listProducts(selected, 50).then((r) => setProducts(r.items)).catch((e) => setErr(e.message)); }, [selected]);

  return (
    <div>
      <h1>Browse by category</h1>
      {err && <p className="err">{err}</p>}
      <div className="chips" data-testid="us-02-category-list">
        {cats.map((c) => (
          <button key={c.name} className={`chip${c.name === selected ? " on" : ""}`} data-testid="us-02-category-item" onClick={() => setSelected(c.name)}>
            {c.name} <span className="muted">({c.count})</span>
          </button>
        ))}
      </div>
      <h2>{selected ?? ""}</h2>
      <ul data-testid="us-02-product-list">
        {products.map((p) => (
          <li key={p.sku} data-testid="us-02-product-item">
            <span><Link to={`/products/${p.sku}`}>{p.name}</Link> <span className="muted">{p.brand}</span></span>
            <span>{inr(p.price_inr)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
