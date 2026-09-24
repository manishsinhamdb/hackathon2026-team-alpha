// us-01: Shopper sees "also bought" recommendations on a product page
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getProduct, getRecommendations, Product, Recommendation } from "../api/client";

const inr = (n: number) => `₹${n.toLocaleString("en-IN")}`;

export function ProductPage() {
  const { sku = "" } = useParams();
  const [product, setProduct] = useState<Product | null>(null);
  const [recos, setRecos] = useState<Recommendation[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setProduct(null); setRecos([]); setErr(null);
    Promise.all([getProduct(sku), getRecommendations(sku, 10)])
      .then(([p, r]) => { setProduct(p); setRecos(r.items); })
      .catch((e) => setErr(e.message));
  }, [sku]);

  if (err) return <p className="err">{err}</p>;
  if (!product) return <p className="muted">Loading…</p>;
  return (
    <div>
      <p className="muted"><Link to="/categories">← Browse</Link></p>
      <h1 data-testid="us-01-product-name">{product.name}</h1>
      <p className="muted">{product.brand} · {product.category} · {inr(product.price_inr)}</p>
      <h2>Customers also bought</h2>
      <ul data-testid="us-01-reco-list">
        {recos.map((r) => (
          <li key={r.sku} data-testid="us-01-reco-item">
            <span><Link to={`/products/${r.sku}`}>{r.name}</Link> <span className="muted">bought together in {r.orders_together} orders</span></span>
            <span>{inr(r.price_inr)}</span>
          </li>
        ))}
      </ul>
      {recos.length === 0 && <p className="muted">No order history for this product yet.</p>}
    </div>
  );
}
