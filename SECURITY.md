# Security policy

## Reporting a vulnerability

Email **security@teamtoken.store** with the details. Please do not open a public
issue for anything that could be exploited before it is fixed. Include what you
did, what happened, and what you expected — a reproduction is worth more than a
severity rating.

We aim to acknowledge within two business days.

## What this server can and cannot do

Understanding the trust boundary usually answers half the question:

- it **holds no credentials of its own**. The caller's TeamToken API key arrives
  in the request and is forwarded to the TeamToken gateway; nothing is stored,
  logged or written to disk;
- it has **no database access** and no access to the TeamToken cabinet. It can
  only call the public API that any holder of that key could call directly;
- generations **cost money**. Anything that could make a tool spend a key more
  than once, or spend a key that is not the caller's, is a security issue here
  even if it looks like a bug.

`TEAMTOKEN_API_BASE` decides where API keys are sent. It is validated at startup
and refuses non-https and non-`teamtoken.store` destinations; overriding that
(`TEAMTOKEN_ALLOW_ANY_BASE=1`) means you accept responsibility for every key that
passes through your deployment.
