# Recommended `main` branch ruleset

The repository is prepared for protected-branch development with stable
Windows CI job names and `CODEOWNERS`.

Configure a GitHub ruleset for `main` with these settings:

- Block branch deletion and force pushes.
- Require pull requests before merging when accepting external contributions.
- Require status checks `test-windows (3.11)` and `test-windows (3.13)`.
- Require branches to be up to date before merging.
- Require conversation resolution for pull requests.
- Optionally require Code Owner review when more maintainers are added.

The ChatGPT GitHub App used to maintain this repository has repository
contents/workflow permissions but not GitHub `administration:write`, which
GitHub requires to create or modify branch rulesets. Therefore the ruleset
itself must be enabled once by the repository owner in **Settings → Rules →
Rulesets**.
