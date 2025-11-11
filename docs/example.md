# Example Output

## Code Review for mr-karan/logchef/pull/57

### Summary
This PR adds an alerting system with Alertmanager integration.

### Critical Issues

**File: `internal/alerts/manager.go:145`**
```go
func (m *Manager) Start() {
    go m.evaluateAlerts()  // No error handling for panics
}
```
**Issue**: Goroutine launched without panic recovery. If `evaluateAlerts()` panics, it will crash silently.
**Fix**: Add panic recovery:
```go
func (m *Manager) Start() {
    go func() {
        defer func() {
            if r := recover(); r != nil {
                log.Printf("Alert evaluation panic: %v", r)
            }
        }()
        m.evaluateAlerts()
    }()
}
```

### Warnings

**File: `internal/sqlite/alerts.go:78`**
- Potential SQL injection in query building - use parameterized queries
- Missing index on `alerts.next_eval_at` - will slow down as alerts grow

### What Looks Good

- Good separation of concerns with Alertmanager client
- Comprehensive test coverage in Vue components
- Clear documentation in `docs/features/alerting.mdx`
