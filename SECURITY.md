# Security Policy

## Supported versions

This is a single-maintainer personal tool with no version branches —
security fixes land on `main` only. There's no LTS/backport commitment.

## Why this matters more than it looks like it should

Notely is designed to run locally for one user, but the Docker packaging
(`docker-compose.yml`) publishes a port, there's no built-in authentication
by default, and the web UI can start pipeline jobs, write files, and change
the stored Anthropic API key on any request it receives. If you widen the
Docker port mapping beyond `127.0.0.1` (e.g. to reach it from another
device on your LAN), you are expected to also set `NOTELY_AUTH_TOKEN` —
see the comments in `docker-compose.yml`. Running it exposed without that
is the main realistic risk case for this project; if you find a way around
that protection, that's exactly the kind of thing to report here.

## Reporting a vulnerability

Please use **GitHub's private vulnerability reporting** for this repo
(the "Report a vulnerability" button under the Security tab) rather than
opening a public issue — that keeps the report private until a fix is
available.

If that's unavailable for some reason, open a regular issue with only
"I found a security issue, how should I send details?" — no specifics —
and a private channel will be worked out from there.

Please include:
- what you found and why it's exploitable (a proof-of-concept beats a
  description)
- the affected file(s)/endpoint(s), if you know them
- what you'd expect to happen instead

There's no bug bounty — this is an unfunded personal project — but real
reports are taken seriously and credited in the fix's commit/changelog
unless you'd rather stay anonymous.
