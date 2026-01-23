"""Tests for Jira integration."""

import pytest
from unittest.mock import patch, MagicMock
from hodor.jira import (
    extract_jira_urls,
    summarize_jira_issue,
    _extract_text_from_adf,
    build_jira_context,
)


class TestExtractJiraUrls:
    def test_single_url(self):
        text = "Fixes https://corotech.atlassian.net/browse/EDR-1966"
        result = extract_jira_urls(text)
        assert result == [("corotech.atlassian.net", "EDR-1966")]

    def test_multiple_urls(self):
        text = """
        Main issue: https://company.atlassian.net/browse/PROJ-123
        Related: https://company.atlassian.net/browse/PROJ-456
        """
        result = extract_jira_urls(text)
        assert result == [
            ("company.atlassian.net", "PROJ-123"),
            ("company.atlassian.net", "PROJ-456"),
        ]

    def test_duplicate_urls(self):
        text = """
        https://company.atlassian.net/browse/PROJ-123
        https://company.atlassian.net/browse/PROJ-123
        """
        result = extract_jira_urls(text)
        assert result == [("company.atlassian.net", "PROJ-123")]

    def test_no_urls(self):
        text = "This MR has no Jira links"
        result = extract_jira_urls(text)
        assert result == []

    def test_empty_text(self):
        result = extract_jira_urls("")
        assert result == []

    def test_none_text(self):
        result = extract_jira_urls(None)
        assert result == []


class TestExtractTextFromAdf:
    def test_simple_paragraph(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Hello world"}],
                }
            ],
        }
        result = _extract_text_from_adf(adf)
        assert result == "Hello world"

    def test_multiple_paragraphs(self):
        adf = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "First"}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "Second"}]},
            ],
        }
        result = _extract_text_from_adf(adf)
        assert result == "First Second"


class TestSummarizeJiraIssue:
    def test_basic_issue(self):
        issue = {
            "key": "EDR-1966",
            "fields": {
                "summary": "Add login validation",
                "issuetype": {"name": "Task"},
                "status": {"name": "In Progress"},
                "priority": {"name": "High"},
                "description": "Implement email validation",
            },
        }
        result = summarize_jira_issue(issue)
        assert "EDR-1966" in result
        assert "Add login validation" in result
        assert "Task" in result
        assert "In Progress" in result
        assert "High" in result

    def test_parent_issue(self):
        issue = {
            "key": "EDR-1000",
            "fields": {
                "summary": "Parent story",
                "issuetype": {"name": "Story"},
                "status": {"name": "Open"},
            },
        }
        result = summarize_jira_issue(issue, is_parent=True)
        assert "Parent Issue" in result


class TestBuildJiraContext:
    @patch("hodor.jira.fetch_jira_issue")
    def test_no_jira_urls(self, mock_fetch):
        result = build_jira_context("Simple title", "No jira links here")
        assert result == ""
        mock_fetch.assert_not_called()

    @patch("hodor.jira.get_parent_issue")
    @patch("hodor.jira.fetch_jira_issue")
    def test_with_jira_url(self, mock_fetch, mock_parent):
        mock_fetch.return_value = {
            "key": "EDR-1966",
            "fields": {
                "summary": "Fix bug",
                "issuetype": {"name": "Bug", "subtask": False},
                "status": {"name": "Open"},
            },
        }
        mock_parent.return_value = None

        result = build_jira_context(
            "Fix https://corotech.atlassian.net/browse/EDR-1966",
            "Description"
        )
        
        assert "## Jira Context" in result
        assert "EDR-1966" in result
        mock_fetch.assert_called_once()

    @patch("hodor.jira.get_parent_issue")
    @patch("hodor.jira.fetch_jira_issue")
    def test_subtask_with_parent(self, mock_fetch, mock_parent):
        mock_fetch.return_value = {
            "key": "EDR-1967",
            "fields": {
                "summary": "Subtask",
                "issuetype": {"name": "Sub-task", "subtask": True},
                "status": {"name": "Open"},
                "parent": {"key": "EDR-1966"},
            },
        }
        mock_parent.return_value = {
            "key": "EDR-1966",
            "fields": {
                "summary": "Parent story",
                "issuetype": {"name": "Story"},
                "status": {"name": "In Progress"},
            },
        }

        result = build_jira_context(
            "https://corotech.atlassian.net/browse/EDR-1967",
            ""
        )
        
        assert "EDR-1967" in result
        assert "EDR-1966" in result
        assert "Parent Issue" in result
