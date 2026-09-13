# Home Center public site deployment

Target host: `home.control-center.pro`.

The public site uses an isolated document root (`/var/www/home-center-public/current`) and an isolated Nginx `server_name`. It does not require editing the existing site's document root or application service.

## Safe rollout

1. Run `python3 tests/validate_public_site.py` from the repository root.
2. Copy `website/` to the target host or check out the exact reviewed revision there.
3. Run `sudo deploy/public-site/deploy.sh website`.
4. Before HTTPS activation, install the HTTP server block and obtain the dedicated certificate for `home.control-center.pro`.
5. Install/enable the complete Nginx vhost, then run `nginx -t` before reload.
6. Verify `/`, `/releases.html`, `/docs.html`, static assets, and `/healthz` over HTTPS.

## Rollback

Point `/var/www/home-center-public/current` to the previous directory under `releases/` and reload Nginx only if the vhost configuration changed. No other virtual host needs to be modified.
