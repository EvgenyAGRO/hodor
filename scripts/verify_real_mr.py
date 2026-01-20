#!/usr/bin/env python3
"""
Verify GitLab integration with REAL credentials.
Usage:
    export GITLAB_TOKEN=...
    python3 scripts/verify_real_mr.py <MR_URL>
"""

import sys
import os
import logging
from unittest.mock import MagicMock
from urllib.parse import urlparse

# Mock OpenHands dependencies to allow importing hodor.agent without full environment
mock_oh = MagicMock()
sys.modules["openhands"] = mock_oh
sys.modules["openhands.sdk"] = mock_oh
sys.modules["openhands.sdk.conversation"] = mock_oh
sys.modules["openhands.sdk.event"] = mock_oh
sys.modules["openhands.sdk.workspace"] = mock_oh
sys.modules["openhands.tools"] = mock_oh
sys.modules["openhands.tools.preset"] = mock_oh
sys.modules["openhands.tools.preset.default"] = mock_oh
sys.modules["litellm"] = mock_oh
sys.modules["dotenv"] = mock_oh

from hodor.gitlab import get_latest_mr_diff_refs, _create_gitlab_client
from hodor.agent import _post_gitlab_inline_review, parse_pr_url

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("verify_real_mr")

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/verify_real_mr.py <MR_URL>")
        sys.exit(1)

    mr_url = sys.argv[1]
    token = os.getenv("GITLAB_TOKEN")
    
    if not token:
        print("❌ Error: GITLAB_TOKEN environment variable is not set.")
        sys.exit(1)

    print(f"🔍 Verifying Hodor on MR: {mr_url}")

    try:
        owner, repo, mr_number, host = parse_pr_url(mr_url)
        print(f"   Parsed: {owner}/{repo} !{mr_number} (Host: {host})")
    except ValueError as e:
        print(f"❌ Error parsing URL: {e}")
        sys.exit(1)

    # 1. Verify Diff Refs (The "AttributeError" check)
    print("\n[1/3] Fetching Diff Refs (Verification of 'versions' fix)...")
    try:
        refs = get_latest_mr_diff_refs(owner, repo, mr_number, host=host)
        print(f"✅ Success! Got refs: {refs}")
    except Exception as e:
        print(f"❌ Failed to get diff refs: {e}")
        # Continue anyway to test posting

    # 2. Verify Parsing (Simulated)
    print("\n[2/3] Verifying Parser Logic...")
    # This sample uses markdown fences to test Tier 1.5 parsing
    sample_output = """
    Here is the analysis:
    ```json
    {
      "findings": [
        {
          "path": "README.md", 
          "line": 1,
          "body": "Test comment from Hodor E2E Verification script.",
          "priority": 3
        }
      ],
      "overall_explanation": "This is a test run to verify inline comments."
    }
    ```
    """
    from hodor.review_parser import parse_review_output
    parsed = parse_review_output(sample_output)
    if not parsed.findings:
         print(f"❌ Parser Failed! Found 0 findings.")
    else:
         print(f"✅ Parser Success! Found {len(parsed.findings)} findings.")

    # 3. Post Inline Comment
    print("\n[3/3] Posting Inline Review (Verification of posting logic)...")
    try:
        # We assume README.md exists and has line 1.
        # If the repo doesn't have README.md, this might fail, but it's a safe bet.
        # Or we can ask user for a file.
        # Ideally, we read the diff to find a valid file, but that's complex.
        # We will try to post.
        
        success = _post_gitlab_inline_review(
            owner, repo, mr_number, sample_output, host=host
        )
        if success:
            print("✅ Successfully attempted to post review.")
            print("   (Check the MR to see if it appeared as a thread or a fallback comment)")
        else:
            print("❌ _post_gitlab_inline_review returned False")

    except Exception as e:
        print(f"❌ Exception during posting: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
