// Generated from ../../../api_contract.yaml — one function per operationId. Base URL from VITE_API_BASE_URL at build time.
const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api";

export interface Product { sku: string; name: string; category: string; brand: string; price_inr: number; }
export interface Category { name: string; count: number; }
export interface Recommendation extends Product { orders_together: number; }
export interface TopProduct extends Product { units: number; }

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) { const body = await r.json().catch(() => ({})); throw new Error(body?.error?.message ?? `HTTP ${r.status}`); }
  return r.json() as Promise<T>;
}

export const getHealth = () => get<{ status: string; db: string }>("/health");
export const listCategories = () => get<{ items: Category[] }>("/categories");
export const listProducts = (category?: string, limit = 50) =>
  get<{ items: Product[]; next_cursor: string | null }>(`/products?${new URLSearchParams({ ...(category ? { category } : {}), limit: String(limit) })}`);
export const getProduct = (sku: string) => get<Product>(`/products/${encodeURIComponent(sku)}`);
export const getRecommendations = (sku: string, limit = 10) => get<{ sku: string; items: Recommendation[] }>(`/products/${encodeURIComponent(sku)}/recommendations?limit=${limit}`);
export const getTopProducts = (days = 30, limit = 10) => get<{ days: number; items: TopProduct[] }>(`/dashboard/top-products?days=${days}&limit=${limit}`);
