import json
import os
from pathlib import Path

import jq
import pytest
from jsonschema import validate
from jsonschema.validators import Draft202012Validator

from ansibleplaybookgrapher import __prog__
from ansibleplaybookgrapher.cli import PlaybookGrapherCLI
from tests import FIXTURES_DIR_PATH, INVENTORY_PATH

# This file directory abspath
DIR_PATH = Path(__file__).parent.resolve()


def run_grapher(
    playbooks: list[str],
    output_filename: str | None = None,
    additional_args: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Utility function to run the grapher
    :param playbooks:
    :param output_filename:
    :param additional_args:
    :return:
    """
    additional_args = additional_args or []
    # Explicitly add verbosity to the tests
    additional_args.insert(0, "-vvv")

    if os.environ.get("TEST_VIEW_GENERATED_FILE") == "1":
        additional_args.insert(0, "--view")

    for idx, p_file in enumerate(playbooks):
        if ".yml" in p_file:
            playbooks[idx] = str(FIXTURES_DIR_PATH / p_file)

    args = [__prog__]

    # Clean the name a little bit
    output_filename = output_filename.replace("[", "-").replace("]", "")
    # put the generated file in a dedicated folder
    args.extend(["-o", str(DIR_PATH / "generated-jsons" / output_filename)])

    args.extend(["--renderer", "json"])
    args.extend(additional_args + playbooks)

    cli = PlaybookGrapherCLI(args)

    return cli.run(), playbooks


def _common_tests(
    json_path: str,
    title: str = "Ansible Playbook Grapher",
    playbooks_number: int = 1,
    plays_number: int = 0,
    tasks_number: int = 0,
    post_tasks_number: int = 0,
    roles_number: int = 0,
    pre_tasks_number: int = 0,
    blocks_number: int = 0,
    handlers_number: int = 0,
) -> dict:
    """Do some checks on the generated JSON files.

    We are using JQ to avoid traversing the JSON ourselves (much easier).
    :param json_path:
    :return:
    """
    with Path(json_path).open() as f:
        output = json.load(f)

    with (FIXTURES_DIR_PATH / "json-schemas/v1.json").open() as schema_file:
        schema = json.load(schema_file)

    # If no exception is raised by validate(), the instance is valid.
    # I currently don't use format but added it here to not forget to add it in case I use in the future.
    validate(
        instance=output,
        schema=schema,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )

    playbooks = jq.compile(".playbooks[]").input(output).all()

    plays = (
        jq.compile(
            '.. | objects | select(.type == "PlayNode" and (.id | startswith("play_")))',
        )
        .input(output)
        .all()
    )

    pre_tasks = (
        jq.compile(
            '.. | objects | select(.type == "TaskNode" and (.id | startswith("pre_task_")))',
        )
        .input(output)
        .all()
    )
    tasks = (
        jq.compile(
            '.. | objects | select(.type == "TaskNode" and (.id | startswith("task_")))',
        )
        .input(output)
        .all()
    )
    post_tasks = (
        jq.compile(
            '.. | objects | select(.type == "TaskNode" and (.id | startswith("post_task_")))',
        )
        .input(output)
        .all()
    )

    roles = (
        jq.compile(
            '.. | objects | select(.type == "RoleNode" and (.id | startswith("role_")))',
        )
        .input(output)
        .all()
    )

    blocks = (
        jq.compile(
            '.. | objects | select(.type == "BlockNode" and (.id | startswith("block_")))',
        )
        .input(output)
        .all()
    )

    handlers = (
        jq.compile(
            '.. | objects | select(.type == "HandlerNode" and (.id | startswith("handler_")))',
        )
        .input(output)
        .all()
    )

    assert output["title"] == title, (
        f"The title should be '{title}' but we found '{output['title']}'"
    )

    assert len(playbooks) == playbooks_number, (
        f"The file '{json_path}' should contains {playbooks_number} playbook(s) but we found {len(playbooks)} playbook(s)"
    )

    assert len(plays) == plays_number, (
        f"The file '{json_path}' should contains {plays_number} play(s) but we found {len(plays)} play(s)"
    )

    assert len(pre_tasks) == pre_tasks_number, (
        f"The file '{json_path}' should contains {pre_tasks_number} pre tasks(s) but we found {len(pre_tasks)} pre tasks"
    )

    assert len(roles) == roles_number, (
        f"The file '{json_path}' should contains {roles_number} role(s) but we found {len(roles)} role(s)"
    )

    assert len(tasks) == tasks_number, (
        f"The file '{json_path}' should contains {tasks_number} tasks(s) but we found {len(tasks)} tasks"
    )

    assert len(post_tasks) == post_tasks_number, (
        f"The file '{json_path}' should contains {post_tasks_number} post tasks(s) but we found {len(post_tasks)} post tasks"
    )

    assert len(blocks) == blocks_number, (
        f"The file '{json_path}' should contains {blocks_number} block(s) but we found {len(blocks)} blocks"
    )

    assert len(handlers) == handlers_number, (
        f"The file '{json_path}' should contains {handlers_number} handler(s) but we found {len(handlers)} handlers"
    )

    # Check the play
    for play in plays:
        assert play.get("colors") is not None, (
            f"The play '{play['name']}' is missing colors'"
        )

    return {
        "tasks": tasks,
        "plays": plays,
        "post_tasks": post_tasks,
        "pre_tasks": pre_tasks,
        "roles": roles,
        "blocks": blocks,
        "handlers": handlers,
    }


def test_simple_playbook(request: pytest.FixtureRequest) -> None:
    """:return:"""
    json_path, playbook_paths = run_grapher(
        ["simple_playbook.yml"],
        output_filename=request.node.name,
        additional_args=[
            "-i",
            str(INVENTORY_PATH),
            "--title",
            "My custom title",
        ],
    )
    _common_tests(
        json_path, plays_number=1, post_tasks_number=2, title="My custom title"
    )


def test_with_block(request: pytest.FixtureRequest) -> None:
    """:return:"""
    json_path, playbook_paths = run_grapher(
        ["with_block.yml"],
        output_filename=request.node.name,
        additional_args=[
            "-i",
            str(INVENTORY_PATH),
        ],
    )
    _common_tests(
        json_path,
        plays_number=1,
        pre_tasks_number=1,
        roles_number=1,
        tasks_number=7,
        blocks_number=4,
        post_tasks_number=2,
    )


def test_with_block_with_skip_tags(
    request: pytest.FixtureRequest,
) -> None:
    """Test with the --skip-tags option with a block.

    :return:
    """
    json_path, playbook_paths = run_grapher(
        ["with_block.yml"],
        output_filename=request.node.name,
        additional_args=[
            "-i",
            str(INVENTORY_PATH),
            "--skip-tags",
            "pre_task_tag",
        ],
    )
    _common_tests(
        json_path,
        plays_number=1,
        pre_tasks_number=0,
        roles_number=1,
        tasks_number=7,
        blocks_number=3,
        post_tasks_number=2,
    )


@pytest.mark.parametrize(
    "flag",
    ["--", "--group-roles-by-name"],
    ids=["no_group", "group"],
)
def test_group_roles_by_name(request: pytest.FixtureRequest, flag: str) -> None:
    """Test when grouping roles by name. This doesn't really affect the JSON renderer: multiple nodes will have the same ID.
    This test ensures that regardless of the flag '--group-roles-by-name', we get the same nodes in the output.

    :param request:
    :return:
    """
    json_path, playbook_paths = run_grapher(
        ["group-roles-by-name.yml"],
        output_filename=request.node.name,
        additional_args=["--include-role-tasks", flag],
    )

    _common_tests(
        json_path,
        plays_number=1,
        roles_number=6,
        tasks_number=9,
        post_tasks_number=8,
        blocks_number=1,
    )


def test_multi_playbooks(request: pytest.FixtureRequest) -> None:
    """:param request:
    :return:
    """
    json_path, playbook_paths = run_grapher(
        ["multi-plays.yml", "relative_var_files.yml", "with_roles.yml"],
        output_filename=request.node.name,
        additional_args=["--include-role-tasks"],
    )

    _common_tests(
        json_path,
        playbooks_number=3,
        plays_number=5,
        pre_tasks_number=4,
        roles_number=10,
        tasks_number=35,
        post_tasks_number=4,
    )


@pytest.mark.parametrize(
    ("flag", "handlers_number"),
    [("--", 0), ("--show-handlers", 6)],
    ids=["no_handlers", "show_handlers"],
)
def test_handlers(
    request: pytest.FixtureRequest, flag: str, handlers_number: int
) -> None:
    """Test for handlers.

    :param request:
    :return:"""
    json_path, playbook_paths = run_grapher(
        ["handlers.yml"],
        output_filename=request.node.name,
        additional_args=[
            "-i",
            str(INVENTORY_PATH),
            "--include-role-tasks",
            flag,
        ],
    )
    _common_tests(
        json_path,
        plays_number=2,
        pre_tasks_number=1,
        tasks_number=6,
        handlers_number=handlers_number,
    )


@pytest.mark.parametrize(
    ("flag", "handlers_number"),
    [("--", 0), ("--show-handlers", 4)],
    ids=["no_handlers", "show_handlers"],
)
def test_handler_in_a_role(
    request: pytest.FixtureRequest, flag: str, handlers_number: int
) -> None:
    """Test for handlers in the role.

    :param request:
    :return:
    """
    json_path, playbook_paths = run_grapher(
        ["handlers-in-role.yml"],
        output_filename=request.node.name,
        additional_args=[
            "-i",
            str(INVENTORY_PATH),
            "--include-role-tasks",
            flag,
        ],
    )
    _common_tests(
        json_path,
        plays_number=1,
        pre_tasks_number=1,
        post_tasks_number=1,
        tasks_number=2,
        handlers_number=handlers_number,
        roles_number=1,
    )


def test_hide_plays_without_roles(request: pytest.FixtureRequest) -> None:
    """Test the --hide-plays-without-roles flag.

    :param request:
    :return:
    """
    json_path, playbook_paths = run_grapher(
        ["play-hiding.yml"],
        output_filename=request.node.name,
        additional_args=[
            "--hide-plays-without-roles",
        ],
    )
    _common_tests(
        json_path,
        plays_number=2,
        roles_number=2,
        tasks_number=1,
    )


@pytest.mark.parametrize(
    ("include_role_tasks_option", "expected_roles_number"),
    [("--", 4), ("--include-role-tasks", 6)],
    ids=["no_include_role_tasks_option", "include_role_tasks_option"],
)
def test_only_roles_with_nested_include_roles(
    request: pytest.FixtureRequest,
    include_role_tasks_option: str,
    expected_roles_number: int,
) -> None:
    """Test graphing a playbook with the --only-roles flag.

    :param request:
    :return:
    """
    json_path, playbook_paths = run_grapher(
        ["nested-include-role.yml"],
        output_filename=request.node.name,
        additional_args=[
            "--only-roles",
            include_role_tasks_option,
        ],
    )

    _common_tests(
        json_path,
        plays_number=1,
        blocks_number=1,
        roles_number=expected_roles_number,
    )
