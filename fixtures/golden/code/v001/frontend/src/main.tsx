import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route, Link, Navigate } from "react-router-dom";
import { CategoriesPage } from "./pages/CategoriesPage";
import { ProductPage } from "./pages/ProductPage";
import { DashboardPage } from "./pages/DashboardPage";

const style = `body{font-family:system-ui,sans-serif;margin:0;background:#f7f7f5;color:#1a1a1a}nav{background:#0f5132;color:#fff;padding:12px 20px;display:flex;gap:20px;align-items:center}nav a{color:#fff;text-decoration:none;font-weight:600}main{max-width:960px;margin:0 auto;padding:20px}h1{font-size:1.4rem}h2{font-size:1.1rem;margin-top:24px}ul{list-style:none;padding:0;margin:0}li{background:#fff;border:1px solid #e3e3e0;border-radius:8px;padding:10px 14px;margin-bottom:8px;display:flex;justify-content:space-between;gap:12px}li a{color:#0f5132;text-decoration:none;font-weight:600}.muted{color:#666;font-size:.9rem}.chips{display:flex;flex-wrap:wrap;gap:8px}.chip{background:#fff;border:1px solid #cfd8d3;border-radius:999px;padding:6px 12px;cursor:pointer}.chip.on{background:#0f5132;color:#fff;border-color:#0f5132}.err{color:#b00020}`;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <style>{style}</style>
    <BrowserRouter>
      <nav>
        <span>Kirana Basket POC</span>
        <Link to="/categories" data-testid="nav-categories">Browse</Link>
        <Link to="/dashboard" data-testid="nav-dashboard">Dashboard</Link>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/categories" replace />} />
          <Route path="/categories" element={<CategoriesPage />} />
          <Route path="/products/:sku" element={<ProductPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
        </Routes>
      </main>
    </BrowserRouter>
  </React.StrictMode>
);
