"""Real implementations of notely.ports's Protocols. Each adapter wraps
exactly one external dependency (an SDK, a subprocess, a library) -- stage
code never imports the wrapped dependency directly once it's on a port."""
