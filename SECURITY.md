# Security

## Reporting a problem

Please do not open a public issue for a security problem. Use GitHub's private reporting instead: on the
repository's **Security** tab, choose **Report a vulnerability**. Say what you found, how to reproduce it, and what
it would let someone do.

Only the current `main` branch is supported.

## What counts

- A way for the harness to send an API key anywhere other than the provider it was given for, or to write one into a
  log, a transcript or a run directory.
- A way for game data to leave the machine without the user's consent (`OPENROUTER_ALLOW_EGRESS=1`).
- A way for a model's reply, a decklist or a puzzle file to run code or write outside the run directory.

A model playing badly, or a rules bug in Forge, is not a security problem; open a normal issue for those.

## Your own keys

`secrets.env` holds your OpenRouter key and is git-ignored. Do not paste a key into an issue, a pull request or a
log you share. If you do, revoke it at the provider first and then tell us.
