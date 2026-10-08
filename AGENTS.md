# Maintainer and agent rules

This project supports scoped reads and explicitly enabled, owner-reviewed writes. Never commit real credentials, private fixtures, personal reading lists, plan databases, or deployment values. Do not change repository visibility, publish release tags, mutate a real library, or provision paid hosting without explicit owner approval.

Run `python -m unittest discover -s tests -v` after changes. Tests must use synthetic fixtures, including simulated upstream writes. Keep configuration, OAuth scope checks, owner-only browser approval, immutable plans, concurrency guards, accurate tool annotations, private receipts and fail-closed uncertainty handling intact. Do not use `confirmed:true` as a replacement for owner review.

Do not add arbitrary URL/account/key/method tools, bypass scope via structural metadata fields, re-run uncertain writes, silently widen collection scope, or promise atomic batches/universal undo. Do not infer real client/library integration from mocked tests. Keep README, Chinese documentation and SECURITY.md aligned with actual capability and deployment status. Read the release checklist before production changes.
