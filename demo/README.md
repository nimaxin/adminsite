# The online demo

The example shop, served at https://adminsite.duckdns.org for anyone to try. Visitors sign in as
admin / admin and may change anything. Every hour `app.py` puts the shop, its history and saved
views back as they started, and empties the uploads.

## Running it here

```bash
uv run --group demo uvicorn demo.app:app
```

Then open http://127.0.0.1:8000. The data goes to `demo-data/`.

Or the whole thing as the server runs it, with Caddy in front on https://localhost:

```bash
DEMO_HOST=localhost DEMO_SECRET_KEY=anything docker compose -f demo/compose.yaml up --build
```

## The server

A Hetzner machine with Ubuntu, Docker and a firewall that lets in SSH, HTTP and HTTPS. SSH takes
keys only.

- `/srv/adminsite-demo/repo` is a clone of this repository, owned by the `deploy` user.
- `/srv/adminsite-demo/.env` holds `DEMO_HOST` and `DEMO_SECRET_KEY`.
- `/srv/adminsite-demo/deploy.sh` is a copy of `deploy.sh`. It is the only command the `deploy`
  user's key may run.

A push to main runs CI. When CI passes, `.github/workflows/demo.yml` connects as `deploy` and
hands the script the commit, which it checks out and rebuilds. The workflow reads the key from
the secret `DEMO_SSH_KEY`, the server's host key from `DEMO_KNOWN_HOSTS`, and the address from
the variable `DEMO_HOST`. Run the workflow by hand to deploy again without a push.

After changing `deploy.sh`, copy it to the server again:

```bash
scp demo/deploy.sh adminsite-demo:/srv/adminsite-demo/deploy.sh
ssh adminsite-demo chmod 755 /srv/adminsite-demo/deploy.sh
```
