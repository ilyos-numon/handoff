import { Container, getContainer } from "@cloudflare/containers";

export interface Env {
  HANDOFF: DurableObjectNamespace<HandoffContainer>;
  // Optional secret (wrangler secret put). Without it the app generates one in its data folder on every boot.
  HANDOFF_AUTH_SECRET?: string;
}

export class HandoffContainer extends Container<Env> {
  defaultPort = 7860; // must match the --port in the Dockerfile CMD
  sleepAfter = "10m";

  constructor(ctx: DurableObjectState<{}>, env: Env) {
    super(ctx, env);
    // Only the auth secret is passed through; the public demo never receives an LLM key.
    this.envVars = env.HANDOFF_AUTH_SECRET ? { HANDOFF_AUTH_SECRET: env.HANDOFF_AUTH_SECRET } : {};
  }
}

// The app keys rate limits on the right-most X-Forwarded-For value, so it must hold
// only the client address Cloudflare saw (CF-Connecting-IP); anything the client sent is dropped.
export function forwardHeaders(req: Request): Request {
  const headers = new Headers(req.headers);
  const clientIp = req.headers.get("CF-Connecting-IP");
  if (clientIp) {
    headers.set("X-Forwarded-For", clientIp);
  } else {
    headers.delete("X-Forwarded-For");
  }
  headers.set("X-Forwarded-Proto", "https");
  return new Request(req, { headers });
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    return getContainer(env.HANDOFF, "demo").fetch(forwardHeaders(req));
  },
};
