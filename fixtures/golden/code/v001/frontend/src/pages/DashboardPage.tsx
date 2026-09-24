// us-03: Category manager sees top-selling products over the last 30 days
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getTopProducts, TopProduct } from "../api/client";

export function DashboardPage() {
  const [items, setItems] = useState<TopProduct[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { getTopProducts(30, 10).then((r) => setItems(r.items)).catch((e) => setErr(e.message)); }, []);
  return (
    <div>
      <h1>Top products — last 30 days</h1>
      {err && <p className="err">{err}</p>}
      <ul data-testid="us-03-top-list">
        {items.map((p, i) => (
          <li key={p.sku} data-testid="us-03-top-item">
            <span>{i + 1}. <Link to={`/products/${p.sku}`}>{p.name}</Link> <span className="muted">{p.category}</span></span>
            <span>{p.units} units</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
