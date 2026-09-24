import express, { Request, Response, NextFunction } from "express";
import mongoose from "mongoose";
import { api } from "./routes.js";

const uri = process.env.MONGODB_URI;
if (!uri) { console.error("MONGODB_URI is required"); process.exit(2); }
const port = parseInt(process.env.PORT || "8080", 10);

const app = express();
app.use(express.json());
app.use((req, _res, next) => { console.log(JSON.stringify({ t: new Date().toISOString(), m: req.method, p: req.originalUrl })); next(); });
app.use("/api", api);
app.use((_req, res) => res.status(404).json({ error: { code: "NOT_FOUND", message: "no such route" } }));
app.use((err: Error, _req: Request, res: Response, _next: NextFunction) => {
  console.error(err);
  res.status(500).json({ error: { code: "INTERNAL", message: err.message } });
});

mongoose.connect(uri, { serverSelectionTimeoutMS: 10000 })
  .then(() => { app.listen(port, () => console.log(`backend listening on ${port}`)); })
  .catch((e) => { console.error("mongo connect failed:", e.message); process.exit(1); });
