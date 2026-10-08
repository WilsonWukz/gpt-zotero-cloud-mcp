# Maintainer and agent rules

Keep the service read-only and account-independent. Never commit real credentials, private fixtures, personal reading lists, or environment values. Do not change repository visibility, publish releases or incur paid hosting without explicit owner approval.

Run `python -m unittest discover -s tests -v` after changes. Core tests use synthetic HTTP fixtures only. Check auth, scope, pagination and sanitized failures before deployment. Never equate an offline test, a healthy process, or configured environment with verified upstream/client integration.

Do not add arbitrary URL/account/key tool arguments, disable OAuth to make a test pass, add undocumented writes, increase worker count, or expose notes/PDFs as a side effect. Read SECURITY.md and the release checklist before changing identity/hosting design.
