# edge: milewise-ai.pages.dev

A Cloudflare Pages project whose only job is a nicer address: one Pages Function
(`functions/[[path]].js`) forwards every request to the Lambda Function URL and passes
the visitor's IP along with a shared secret, so per-IP limits keep working
(design.md §4.8). The page itself is still served by the API.

    npx wrangler login     # once
    make edge-deploy       # sets ORIGIN_URL / EDGE_SECRET from Terraform, then deploys
