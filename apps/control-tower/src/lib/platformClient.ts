// Server-only cached PlatformClient, shared by the BFF routes that talk to the platform invoke / runtime
// APIs (chat, session stop, session status). One instance keeps the OAuth token cache warm across requests.
import "server-only";
import { getServerConfig } from "./env";
import { PlatformClient } from "./platform";

let client: PlatformClient | null = null;

export function getPlatformClient(): PlatformClient {
  if (client) return client;
  const cfg = getServerConfig();
  client = new PlatformClient({
    baseUrl: cfg.platformBaseUrl,
    projectId: cfg.projectId,
    chatWorkspaceId: cfg.chatWorkspaceId,
    clientId: cfg.saClientId,
    clientSecret: cfg.saClientSecret,
  });
  return client;
}
