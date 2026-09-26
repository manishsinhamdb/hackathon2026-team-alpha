// Server-only configuration. Importing this from a Client Component is a build error because it
// reads process.env values that are never exposed with NEXT_PUBLIC_. Keep every secret here.
import "server-only";

export interface ServerConfig {
  platformBaseUrl: string;
  projectId: string;
  chatWorkspaceId: string;
  saClientId: string;
  saClientSecret: string;
  mongoUri: string;
  dbName: string;
  uiUserId: string;
}

function required(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`missing required env var ${name}`);
  return v;
}

let cached: ServerConfig | null = null;

export function getServerConfig(): ServerConfig {
  if (cached) return cached;
  cached = {
    platformBaseUrl: (process.env.PLATFORM_BASE_URL || "https://agentic-platform.mongodb.com").replace(/\/+$/, ""),
    projectId: required("PLATFORM_PROJECT_ID"),
    chatWorkspaceId: required("CHAT_WORKSPACE_ID"),
    saClientId: required("PLATFORM_SA_CLIENT_ID"),
    saClientSecret: required("PLATFORM_SA_CLIENT_SECRET"),
    mongoUri: required("POC_PLATFORM_MONGODB_URI"),
    dbName: process.env.POC_PLATFORM_DB || "poc_builder",
    uiUserId: process.env.UI_USER_ID || "u_ui",
  };
  return cached;
}

// Optional S3 access for the Artefacts card (Round 5). The same IAM user the agents use; when the key pair
// is absent the artefact list falls back to DB-derived keys and "open" is disabled. Never sent to the browser.
export interface S3Config {
  bucket: string;
  region: string;
  credentials: { accessKeyId: string; secretAccessKey: string; sessionToken?: string } | null;
}

export function getS3Config(): S3Config {
  const accessKeyId = process.env.AWS_ACCESS_KEY_ID || "";
  const secretAccessKey = process.env.AWS_SECRET_ACCESS_KEY || "";
  return {
    bucket: process.env.POC_S3_BUCKET || "msinha-hackathon",
    region: process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION || "ap-south-1",
    credentials:
      accessKeyId && secretAccessKey
        ? { accessKeyId, secretAccessKey, ...(process.env.AWS_SESSION_TOKEN ? { sessionToken: process.env.AWS_SESSION_TOKEN } : {}) }
        : null,
  };
}
