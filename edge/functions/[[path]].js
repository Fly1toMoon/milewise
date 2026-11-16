// Cloudflare Pages Function: milewise-ai.pages.dev forwards every request to the Lambda
// Function URL (design.md §4.8). Responses stream through as they arrive (SSE answers).
//
// Lambda would see a Cloudflare address as the caller, which would put every visitor in
// one per-IP limit, so the visitor's address goes along in X-Viewer-IP with the shared
// EDGE_SECRET; the API trusts X-Viewer-IP only with that secret. Both headers are set
// here, so a visitor cannot supply their own.
//
// Pages secrets (set by `make edge-deploy`): ORIGIN_URL, EDGE_SECRET.
export async function onRequest({ request, env }) {
  const url = new URL(request.url);
  const target = new URL(url.pathname + url.search, env.ORIGIN_URL);
  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.set("x-edge-secret", env.EDGE_SECRET);
  headers.set("x-viewer-ip", request.headers.get("cf-connecting-ip") || "");
  return fetch(target, {
    method: request.method,
    headers,
    body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
    redirect: "manual",
  });
}
