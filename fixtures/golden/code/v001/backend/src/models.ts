import mongoose, { Schema } from "mongoose";

export interface Product { sku: string; name: string; category: string; brand: string; price_inr: number; created_at: Date; }
export interface OrderItem { sku: string; qty: number; unit_price_inr: number; }
export interface Order { order_id: string; customer_id: string; ordered_at: Date; items: OrderItem[]; total_inr: number; }

const productSchema = new Schema<Product>({
  sku: { type: String, required: true, unique: true },
  name: { type: String, required: true },
  category: { type: String, required: true },
  brand: { type: String, required: true },
  price_inr: { type: Number, required: true },
  created_at: { type: Date, required: true },
}, { collection: "products", versionKey: false });
productSchema.index({ category: 1, name: 1 });

const orderSchema = new Schema<Order>({
  order_id: { type: String, required: true, unique: true },
  customer_id: { type: String, required: true },
  ordered_at: { type: Date, required: true },
  items: [{ sku: String, qty: Number, unit_price_inr: Number }],
  total_inr: { type: Number, required: true },
}, { collection: "orders", versionKey: false });
orderSchema.index({ "items.sku": 1, ordered_at: -1 });
orderSchema.index({ ordered_at: -1 });

export const ProductModel = mongoose.model<Product>("Product", productSchema);
export const OrderModel = mongoose.model<Order>("Order", orderSchema);
