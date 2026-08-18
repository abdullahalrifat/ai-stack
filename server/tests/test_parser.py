import pytest

from app.agent.parser import (
    ParserError,
    extract_final_answer,
    extract_json,
    parse_plan,
    parse_tool_arguments,
    validate_action,
    validate_agent_response,
)

# ----------------------------------------------------
# extract_json
# ----------------------------------------------------


def test_extract_json_plain():
    text = '{"tool":"read_file","args":{"file_path":"README.md"}}'

    result = extract_json(text)

    assert result["tool"] == "read_file"


def test_extract_json_markdown():
    text = """
    ```json
    {
        "tool":"read_file",
        "args":{
            "file_path":"README.md"
        }
    }
    ```
    """

    result = extract_json(text)

    assert result["tool"] == "read_file"


def test_extract_json_with_extra_text():
    text = """
    Here is the result.

    {
        "final_answer":"Hello world"
    }

    Thanks.
    """

    result = extract_json(text)

    assert result["final_answer"] == "Hello world"


def test_extract_json_empty():

    with pytest.raises(ParserError):
        extract_json("")


def test_extract_json_invalid():

    with pytest.raises(ParserError):
        extract_json("{invalid json}")


def test_extract_json_not_found():

    with pytest.raises(ParserError):
        extract_json("hello world")


# ----------------------------------------------------
# parse_tool_arguments
# ----------------------------------------------------


def test_parse_tool_arguments_dict():

    args = {"directory": "."}

    assert parse_tool_arguments(args) == args


def test_parse_tool_arguments_json_string():

    args = '{"directory":"."}'

    result = parse_tool_arguments(args)

    assert result["directory"] == "."


def test_parse_tool_arguments_plain_string():

    result = parse_tool_arguments("hello")

    assert result == {"input": "hello"}


def test_parse_tool_arguments_none():

    assert parse_tool_arguments(None) == {}


def test_parse_tool_arguments_invalid_type():

    with pytest.raises(ParserError):
        parse_tool_arguments(123)


# ----------------------------------------------------
# extract_final_answer
# ----------------------------------------------------


def test_extract_final_answer():

    answer = extract_final_answer('{"final_answer":"Completed"}')

    assert answer == "Completed"


def test_extract_final_answer_missing():

    answer = extract_final_answer('{"tool":"list_files"}')

    assert answer is None


# ----------------------------------------------------
# validate_action
# ----------------------------------------------------


def test_validate_action_valid():

    assert validate_action(
        {
            "tool": "list_files",
            "args": {},
        }
    )


def test_validate_action_missing_tool():

    assert not validate_action(
        {
            "args": {},
        }
    )


# ----------------------------------------------------
# validate_agent_response
# ----------------------------------------------------


def test_validate_agent_response_tool():

    assert validate_agent_response(
        {
            "tool": "list_files",
            "args": {},
        }
    )


def test_validate_agent_response_invalid_tool():

    assert not validate_agent_response(
        {
            "tool": 123,
            "args": {},
        }
    )


def test_validate_agent_response_final_answer():

    assert validate_agent_response(
        {"final_answer": "This is a sufficiently long final answer for validation."}
    )


def test_validate_agent_response_short_answer():

    assert not validate_agent_response({"final_answer": "Too short"})


def test_validate_agent_response_invalid():

    assert not validate_agent_response({})


# ----------------------------------------------------
# parse_plan
# ----------------------------------------------------


def test_parse_plan():

    response = """
    {
        "thought":"Inspect repository",
        "actions":[
            {
                "tool":"list_files",
                "args":{
                    "directory":"."
                }
            },
            {
                "tool":"read_file",
                "args":{
                    "file_path":"README.md"
                }
            }
        ]
    }
    """

    plan = parse_plan(response)

    assert len(plan.steps) == 2

    assert plan.steps[0].tool == "list_files"

    assert plan.steps[1].tool == "read_file"


def test_parse_plan_missing_tool():

    response = """
    {
        "thought":"Nothing",
        "actions":[
            {
                "args":{}
            }
        ]
    }
    """

    plan = parse_plan(response)

    assert len(plan.steps) == 0
