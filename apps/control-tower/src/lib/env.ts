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
