# Contributing

Run `python -m unittest discover -s tests -v` before proposing changes. Tests must use synthetic fixtures and `httpx.MockTransport`, never live libraries or production secrets. CI must remain read-only and safe for untrusted pull requests.

Authentication, subtree enforcement, pagination, error sanitization and read-only guarantees need regression tests. Treat titles and abstracts as data, not instructions. Tool schema changes need a changelog entry. Do not add library writes or multi-user support as an incidental refactor; each needs a separate design/security review.

No private account IDs, emails, reading-list exports, PDF content, credentials or authorization logs in commits. Follow SECURITY.md for vulnerability reporting and docs/RELEASING.md before publication.
