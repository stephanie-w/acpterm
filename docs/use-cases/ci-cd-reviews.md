# CI/CD Automated PR & Code Security Reviews

This guide explains how to run `acpterm` in CI/CD pipelines (e.g. GitHub Actions, GitLab CI) to perform automated PR reviews, security scanning, and diff analysis.

---

## 🚀 Key Advantages in CI/CD

1. **Read-Only Safety (`--read-only`)**: Prevents the agent from accidentally modifying workspace files or corrupting repository state during a CI pipeline check.
2. **Headless Execution (`exec`)**: One-shot execution mode designed for non-interactive runner environments.
3. **Structured Transcript Export (`--export`)**: Generates a clean Markdown report containing the review summary, findings, and metadata.

---

## 🛠️ GitHub Actions Workflow Example

Save this workflow to `.github/workflows/acpterm-pr-review.yml` in your repository:

```yaml
name: Automated ACP PR Review

on:
  pull_request:
    types: [opened, synchronize]

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Install uv
        uses: astral-sh/setup-uv@v3

      - name: Install acpterm
        run: |
          uv tool install acpterm

      - name: Run Automated PR Review
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: |
          # Generate git diff between PR branch and base branch
          git diff origin/${{ github.base_ref }}...HEAD > pr_diff.patch

          # Run acpterm in read-only mode passing diff via stdin
          cat pr_diff.patch | acpterm --read-only exec \
            "Review this git diff for bugs, performance bottlenecks, and security risks. Provide actionable feedback." \
            --export pr_review.md

      - name: Post PR Review Comment
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            const reviewText = fs.readFileSync('pr_review.md', 'utf8');
            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: reviewText
            });
```

---

## 📋 Security Policy Hook in CI (`-p / --on-permission`)

To strictly enforce file reading or command execution boundaries in CI, use a permission policy script:

```bash
acpterm --read-only exec "Analyze changes" -p "python3 scripts/ci_permission_policy.py"
```

### `scripts/ci_permission_policy.py`

```python
#!/usr/bin/env python3
import os
import sys

kind = os.environ.get("ACPTERM_PERMISSION_KIND", "")
path = os.environ.get("ACPTERM_PERMISSION_PATH", "")

# Block access to secrets or sensitive environments in CI
sensitive_files = [".env", "secrets.json", "credentials.json"]
if any(s in path for s in sensitive_files):
    print(f"[ci:blocked] Denied access to sensitive path: {path}")
    sys.exit(1)  # Deny permission

# Allow safe file reads
if kind == "read":
    sys.exit(0)  # Approve permission

sys.exit(1)  # Deny all non-read operations in CI
```
