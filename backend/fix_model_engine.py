"""
Fix Model Engine Patch Script (QUARANTINED)

Orphan patch script — do not run. Top-level code rewrites
services/model_engine.py in place on execution; no production caller
imports or invokes this module. Retained only for historical reference.
"""

QUARANTINED = True

if __name__ == "__main__":
    raise SystemExit("quarantined: orphan patch script (fix_model_engine.py) - do not run; retained for historical reference only")
    # Read the file
    with open('services/model_engine.py', 'r') as f:
        content = f.read()

    # Find and replace the _parse_decision method's tool_calls handling
    old_block = '''                # We accept multiple tool calls and execute them sequentially.
                # This allows models to batch related operations (e.g., research + complete_step)
                # in a single turn, reducing round-trips.
                if len(tool_calls) > 1:
                    logger.info(
                        "Model returned %d tool calls; executing sequentially",
                        len(tool_calls),
                    )

                tool_call = tool_calls[0]
                function = tool_call.get('function', {})
                decision_type = function.get('name', '')

                # Validate decision type
                valid_types = [
                    DecisionType.COMPLETE_TASK,
                    DecisionType.FAIL_TASK,
                    DecisionType.REQUEST_USER_INPUT,
                    DecisionType.RUN_SKILL,
                    DecisionType.EXECUTE_TOOL
                ]

                # Parse arguments (Ollama may return an object or a JSON string)
                raw_args = function.get('arguments', {})
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError as e:
                        raise ModelDecisionError(
                            code="INVALID_JSON",
                            message=f"Failed to parse arguments: {str(e)}"
                        )
                else:
                    args = raw_args or {}

                if decision_type not in valid_types:'''

    new_block = '''                # Parse all tool calls into a list for sequential execution
                parsed_tool_calls = []
                for tc in tool_calls:
                    fn = tc.get('function', {})
                    dt = fn.get('name', '')
                    raw = fn.get('arguments', {})
                    if isinstance(raw, str):
                        try:
                            parsed_args = json.loads(raw)
                        except json.JSONDecodeError as e:
                            raise ModelDecisionError(
                                code="INVALID_JSON",
                                message=f"Failed to parse arguments: {str(e)}"
                            )
                    else:
                        parsed_args = raw or {}
                    parsed_tool_calls.append({"decision_type": dt, "args": parsed_args})

                if len(parsed_tool_calls) > 1:
                    logger.info(
                        "Model returned %d tool calls; executing sequentially",
                        len(parsed_tool_calls),
                    )

                # First tool call is the primary decision
                primary = parsed_tool_calls[0]
                decision_type = primary["decision_type"]
                args = primary["args"]

                # Validate decision type
                valid_types = [
                    DecisionType.COMPLETE_TASK,
                    DecisionType.FAIL_TASK,
                    DecisionType.REQUEST_USER_INPUT,
                    DecisionType.RUN_SKILL,
                    DecisionType.EXECUTE_TOOL
                ]

                if decision_type not in valid_types:'''

    if old_block in content:
        content = content.replace(old_block, new_block)
        with open('services/model_engine.py', 'w') as f:
            f.write(content)
        print("Replacement successful!")
    else:
        print("Old block not found!")
        # Let's find what's actually there
        idx = content.find("We accept multiple tool calls")
        if idx >= 0:
            print("Found at:", idx)
            print(content[idx:idx+500])
        else:
            print("Not found at all")
