# Skills System for Repo-Specific Review Guidelines

Hodor supports OpenHands' skills system, allowing you to customize reviews with repository-specific guidelines. This is particularly useful for:
- Project-specific coding standards
- Architecture patterns to enforce
- Common bugs to watch for
- Security requirements
- Performance considerations

## Quick Start

Create a `.cursorrules` file in your repository root:

```markdown
# Code Review Guidelines for MyProject

## Architecture
- All API handlers must use the RequestValidator middleware
- Database queries must use prepared statements
- Authentication required for all /api/* endpoints

## Security
- Never log sensitive data (passwords, tokens, PII)
- All user input must be sanitized before database queries
- Rate limiting required on public endpoints

## Performance
- Database queries in loops are NOT allowed (use batch queries)
- Cache expensive computations (>100ms) using Redis
- Image uploads must be resized before storage

## Common Bugs
- Check for null/undefined before accessing nested properties
- Always handle promise rejections
- Validate array length before accessing elements
```

## Skills System Overview

Hodor automatically loads repository-specific review guidelines through the OpenHands skills system. Skills are injected into the agent's context when reviewing PRs.

### Supported Skill Locations

Hodor looks for skills in these locations (in priority order):

1. **`.cursorrules`** - Simple, single-file project guidelines
2. **`agents.md` or `agent.md`** - Alternative single-file location
3. **`.hodor/skills/*.md`** - Modular skills (multiple files organized by topic)

All files are loaded automatically when the workspace is set up. No configuration needed.

### 1. Simple Skills (Single File)

Use `.cursorrules` for straightforward project guidelines:

**Location**: `.cursorrules` in repository root

**Format**:
```markdown
# Review Guidelines

Your project-specific instructions here...
```

**When Loaded**: Automatically on every PR review

**Use Case**: Project-wide conventions that apply to all PRs

### 2. Modular Skills (Multiple Files)

Use `.hodor/skills/` for organized, topic-specific guidelines:

**Location**: `.hodor/skills/TOPIC.md`

**Format**:
```markdown
---
triggers:
  - security
  - auth
  - authentication
---

# Security Review Guidelines

When reviewing security-related changes:
- Check for SQL injection vulnerabilities
- Verify authentication is required
- Ensure sensitive data is encrypted
- ...
```

**When Loaded**: Automatically when PR title/description contains trigger keywords

**Use Case**: Domain-specific checks (security, performance, database, etc.)

**Format**:
```markdown
# Security Review Guidelines

Security-specific checks for authentication, authorization, data validation...
```

**When Loaded**: Automatically with all other skills

**Use Case**: Organize guidelines by domain (security, performance, database, testing, etc.)

### 3. Advanced: Triggered Skills (Not Yet Supported)

Future enhancement - skills that activate based on PR keywords:

**Format**:
```markdown
---
triggers:
  - task
schema:
  properties:
    ticket_id:
      type: string
      description: JIRA ticket ID
---

# Review Against Requirements

Verify PR implements requirements from ticket {{ticket_id}}...
```

**When Loaded**: When user provides required input

**Use Case**: Checking PR against specific requirements or tickets

## Examples

### Example 1: Python Project Guidelines

**.cursorrules**:
```markdown
# Python Code Review Guidelines

## Style
- Follow PEP 8 (enforced by ruff)
- Type hints required for all public functions
- Docstrings required for classes and public methods

## Common Issues
- Check for bare `except:` clauses (should specify exception type)
- Ensure `with` statement used for file/resource handling
- Verify async functions properly await coroutines

## Testing
- Unit tests required for new features
- Test coverage must not decrease
- Mock external dependencies (APIs, databases)

## Security
- Never use `eval()` or `exec()`
- Validate all user inputs
- Use parameterized queries (never string concatenation for SQL)
```

### Example 2: JavaScript/TypeScript Project

**agents.md**:
```markdown
# Frontend Code Review Standards

## React Components
- Use functional components with hooks (no class components)
- PropTypes or TypeScript interfaces required
- Extract reusable logic into custom hooks
- Memoize expensive computations with useMemo/useCallback

## State Management
- Use React Query for server state
- Use Context for global UI state only
- Don't store derived data in state

## Performance
- Lazy load routes with React.lazy()
- Optimize images (WebP format, appropriate sizes)
- Check bundle size impact (run `npm run bundle-analyze`)

## Common Bugs
- Check for missing dependency arrays in useEffect
- Verify exhaustive deps in useCallback/useMemo
- Look for potential infinite render loops
```

### Example 3: Security-Focused Review

**.hodor/skills/security.md**:
```markdown
# Security Review Checklist

When reviewing security-related code:

## Authentication
- [ ] Passwords hashed with bcrypt/argon2 (never MD5/SHA1)
- [ ] Session tokens are cryptographically random
- [ ] Token expiry implemented
- [ ] Rate limiting on login endpoints

## Authorization
- [ ] User permissions checked before operations
- [ ] No IDOR vulnerabilities (user can't access others' data)
- [ ] Admin checks on privileged operations

## Input Validation
- [ ] All user input validated and sanitized
- [ ] SQL injection prevented (parameterized queries)
- [ ] XSS prevented (proper escaping)
- [ ] File upload restrictions (type, size, content validation)

## Sensitive Data
- [ ] No secrets in code (use environment variables)
- [ ] Sensitive data not logged
- [ ] HTTPS enforced for sensitive operations
- [ ] Secure cookie flags set (HttpOnly, Secure, SameSite)
```

### Example 4: Database Review

**.hodor/skills/database.md**:
```markdown
# Database Change Review

## Schema Changes
- [ ] Migration is reversible (has down migration)
- [ ] Indexes added for foreign keys
- [ ] No breaking changes without deprecation period
- [ ] Column names follow naming convention

## Query Performance
- [ ] No N+1 query patterns
- [ ] Appropriate indexes for WHERE/JOIN clauses
- [ ] LIMIT used for potentially large result sets
- [ ] Explain plan checked for slow queries

## Data Integrity
- [ ] Foreign key constraints defined
- [ ] NOT NULL constraints where appropriate
- [ ] Unique constraints on natural keys
- [ ] Default values make sense
```

## Advanced: MCP Integration

*(Not yet implemented)* Future enhancement - integrate MCP (Model Context Protocol) tools for richer context:

**.hodor/skills/github-integration.md**:
```markdown
# GitHub Integration Review (Future)

Could fetch additional context via MCP:
- Previous PRs by the same author
- Related issues and discussions
- CI/CD check results
- Code owners and reviewers
```

## How Hodor Loads Skills

When Hodor starts a review:

1. **Workspace Setup**: Clone repo and checkout PR branch
2. **Skill Discovery** (via OpenHands SDK): Scan for:
   - `.cursorrules` (simple, single-file guidelines)
   - `agents.md` or `agent.md` (alternative single-file location)
   - `.hodor/skills/*.md` (modular, multi-file guidelines)
3. **Context Building**: All discovered skills are combined into the agent's system prompt
4. **Review**: Agent uses combined guidelines to review the PR

**Note**: All skills are loaded on every review. Keyword-based triggering is not yet implemented.

## Best Practices

### DO:
- ✅ Keep guidelines concise and actionable
- ✅ Focus on project-specific patterns (not general best practices)
- ✅ Include examples of bad patterns to avoid
- ✅ Update skills as project evolves
- ✅ Use keyword triggers for optional deep-dives

### DON'T:
- ❌ Don't duplicate general coding advice (Hodor already knows this)
- ❌ Don't make guidelines too long (AI context limits)
- ❌ Don't include outdated or deprecated patterns
- ❌ Don't overlap skills (consolidate related guidelines)

## Testing Your Skills

Test your skills locally before committing:

```bash
# Review a PR with your local skills
cd /path/to/your/repo
hodor https://github.com/owner/repo/pull/123 --verbose

# The verbose flag will show which skills were loaded
```

## Troubleshooting

### Skills Not Loading?

1. Check file location (must be in repo root or `.hodor/skills/`)
2. Verify filename (`.cursorrules`, `agents.md`, or `.hodor/skills/*.md`)
3. Ensure files are in the repository being reviewed (not in Hodor's repo)
4. Run with `--verbose` and check agent logs for skill discovery

### Skills Too Generic?

Remember: Hodor already knows general best practices. Your skills should focus on:
- Project-specific architecture patterns
- Common bugs in YOUR codebase
- Team conventions and standards
- Domain-specific requirements (finance, healthcare, etc.)

## Future Enhancements

Coming soon:
- [ ] Per-directory skills (e.g., different rules for frontend/ vs backend/)
- [ ] Skill inheritance (team-wide + project-specific)
- [ ] MCP server integration for external data
- [ ] Skill testing framework
- [ ] Skills marketplace (community-contributed patterns)

## Questions?

See [OpenHands Skills Documentation](https://docs.openhands.dev/sdk/arch/skill) for more details on the underlying skills system.
