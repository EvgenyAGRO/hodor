from hodor.review_parser import parse_review_output

def test_parse_markdown_fenced_json():
    text = """
    Here is the review output:
    
    ```json
    {
        "findings": [
            {
                "path": "src/main.py",
                "line": 10,
                "body": "Bug found",
                "priority": 1
            }
        ]
    }
    ```
    
    Hope this helps!
    """
    
    parsed = parse_review_output(text)
    assert len(parsed.findings) == 1
    assert parsed.findings[0].body == "Bug found"

def test_parse_markdown_fenced_json_no_lang():
    text = """
    ```
    {
        "findings": [
            {
                "path": "test.py",
                "line": 1,
                "body": "Issue",
                "priority": 2
            }
        ]
    }
    ```
    """
    parsed = parse_review_output(text)
    assert len(parsed.findings) == 1
    assert parsed.findings[0].code_location.absolute_file_path.name == "test.py"
