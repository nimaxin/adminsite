# The online demo

The example shop and the field gallery (`examples/fields.py`), served at
https://adminsite.duckdns.org for anyone to try. Visitors sign in as admin / admin and may change
anything, each on a copy of their own, so nobody sees what anyone else typed.

At startup `app.py` writes the shop and the gallery as they start, with the gallery's sample
files, to `template/` in the data folder. A cookie tells visitors apart, and the first time it
comes back the template is copied to `visitors/<cookie>/`, with an admin of its own. A client
that keeps no cookies never gets a copy. A copy nobody has used for an hour is thrown away, and
at most 500 are kept, the least recently used going first; one takes about 0.3 MB of memory
and 125 KB of the data folder.

Everyone signs in as the same admin, so the audit log keeps what visitors did but not their sign
ins, IP addresses or browsers.

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
