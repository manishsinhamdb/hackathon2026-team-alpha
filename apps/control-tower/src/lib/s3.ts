// The real ArtifactStore (Round 5, item 2): list objects under a POC prefix and presign ONE GetObject.
// Server-only — the AWS key pair stays in the server env and never reaches the browser, which only ever
// receives a short-lived (10 min) presigned GET URL for a key the BFF has validated.
import "server-only";
import { GetObjectCommand, ListObjectsV2Command, S3Client } from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import { clampPresignTtl, type ArtifactStore } from "./artifacts";
import { getS3Config } from "./env";

const MAX_KEYS = 2000; // a POC prefix holds generated sources too; cap the walk

let cached: { store: ArtifactStore } | null = null;

// null when the S3 key pair is not configured (the list route then falls back to DB-derived keys).
export function getArtifactStore(): ArtifactStore | null {
  if (cached) return cached.store;
  const cfg = getS3Config();
  if (!cfg.credentials) return null;
  const client = new S3Client({ region: cfg.region, credentials: cfg.credentials });
  const store: ArtifactStore = {
    async list(prefix) {
      const out: Array<{ key: string; size?: number; last_modified?: string }> = [];
      let token: string | undefined;
      do {
        const res = await client.send(
          new ListObjectsV2Command({ Bucket: cfg.bucket, Prefix: prefix, ContinuationToken: token, MaxKeys: 1000 }),
        );
        for (const o of res.Contents ?? []) {
          if (o.Key) out.push({ key: o.Key, size: o.Size, last_modified: o.LastModified?.toISOString() });
        }
        token = res.IsTruncated ? res.NextContinuationToken : undefined;
      } while (token && out.length < MAX_KEYS);
      return out;
    },
    async presign(key, expiresSec) {
      return getSignedUrl(client, new GetObjectCommand({ Bucket: cfg.bucket, Key: key }), {
        expiresIn: clampPresignTtl(expiresSec),
      });
    },
  };
  cached = { store };
  return store;
}
