# Later production deployment steps

These are preparation notes only. No deployment, DNS, or TLS work is performed by this local-development lane.

1. Push the repository to the chosen Git host.
2. Create a Coolify application from that repository.
3. Attach a persistent temporary volume only if restart-spanning job cleanup/recovery is required.
4. Configure a domain such as `slideshow.kratoslab.com`.
5. Add the subdomain DNS record in Namecheap pointing to the existing server.
6. Configure and verify Coolify HTTPS.
7. Add production request and job rate limiting.
8. Add CAPTCHA or equivalent abuse protection.
9. Set production CPU, memory, PID, disk, upload-body, and render-time quotas.
10. Confirm scheduled cleanup, free-space monitoring, and alerts.
11. Run a public security smoke test, including malformed uploads, authorization isolation, URL/log token leakage, resource exhaustion, and expiry.
