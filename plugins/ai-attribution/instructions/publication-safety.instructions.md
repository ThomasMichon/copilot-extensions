---
applyTo: "**"
---

# Publication safety fallback

**Fallback policy `[owner: ai-attribution@0.1.0-dev16]`:** Before publishing, determine the audience of this specific contribution (self-authored, a reply to an automated review bot, or a response to a PR/issue/thread another party authored or participates in) and whether the target host is configured as operator-only (`internal_host`). Disclose AI assistance prominently for a contribution addressing another party, in every repository -- public or private, including one the operator owns; an internal host or a self-authored/bot-reply contribution may omit it by default. Every public artifact must remain persona-neutral and exclude credentials, private identifiers, internal hosts or paths, account details, record IDs, and private rationale. Use generic placeholders, follow the target repository's conventions, and audit the published result. Invoke the `ai-attribution` skill for the complete publication workflow.
