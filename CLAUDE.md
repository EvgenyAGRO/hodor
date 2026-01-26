# Claude Code Instructions for Hodor

## Testing Requirements

**ALWAYS run all tests after making changes to verify no regressions were introduced.**

Run tests with:
```bash
python -m pytest tests/ -v -c /dev/null
```

Or for specific test files:
```bash
python -m pytest tests/test_<name>.py -v -c /dev/null
```

Note: The `-c /dev/null` flag is needed to avoid pytest config issues with coverage options in pyproject.toml.

## Test Import Pattern

When creating new test files that import from `hodor`, use direct module loading to avoid openhands SDK dependency issues:

```python
import importlib.util
from pathlib import Path

# Load module directly (avoids package import issues)
_module_path = Path(__file__).parent.parent / "hodor" / "<module_name>.py"
_spec = importlib.util.spec_from_file_location("<module_name>", _module_path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

# Import functions from the loaded module
function_name = _module.function_name
```
