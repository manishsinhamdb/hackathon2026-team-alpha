// READ-ONLY access to the platform DB. A single cached MongoClient is reused across requests (and
// across hot reloads in dev via a global) so we don't open a pool per request. The UI never writes.
import "server-only";
import { MongoClient, type Db } from "mongodb";
import { getServerConfig } from "./env";

declare global {
  // eslint-disable-next-line no-var
  var __controlTowerMongo: Promise<MongoClient> | undefined;
}

function clientPromise(): Promise<MongoClient> {
  const cfg = getServerConfig();
  if (!global.__controlTowerMongo) {
    const client = new MongoClient(cfg.mongoUri, {
      serverSelectionTimeoutMS: 10_000,
      // Defence in depth: the UI is read-only. A secondary/primary read preference is fine.
      readPreference: "primaryPreferred",
    });
    global.__controlTowerMongo = client.connect();
  }
  return global.__controlTowerMongo;
}

export async function getDb(): Promise<Db> {
  const cfg = getServerConfig();
  const client = await clientPromise();
  return client.db(cfg.dbName);
}
