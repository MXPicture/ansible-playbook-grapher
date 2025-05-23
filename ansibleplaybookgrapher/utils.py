# Copyright (C) 2024 Mohamed El Mouctar HAIDARA
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
# ruff: noqa: PTH118,PTH120,PTH117,PTH110,PTH116
import hashlib
import os
import uuid
from collections import defaultdict
from itertools import chain
from operator import methodcaller
from typing import Any

from ansible.errors import AnsibleError
from ansible.module_utils.common.text.converters import to_text
from ansible.parsing.dataloader import DataLoader
from ansible.playbook.role_include import IncludeRole
from ansible.playbook.task import Task
from ansible.playbook.task_include import TaskInclude
from ansible.template import Templar
from ansible.utils.display import Display
from colour import Color

display = Display()


def convert_when_to_str(when: list) -> str:
    """Convert ansible conditional when to str.

    :param when:
    :return:
    """
    if len(when) == 0:
        return ""

    # Convert each element in the list to str
    when_to_str = list(map(str, when))
    return f"[when: {' and '.join(when_to_str)}]".strip().replace("\n", "")


def hash_value(value: str) -> str:
    """Convert name to md5 to avoid issues with special chars.

    The ID are not visible to end user in web/rendered graph so we do
    not have to care to make them look pretty.
    There are chances for hash collisions, but we do not care for that so much in here.
    :param value: string which represents id
    :return: string representing a hex hash.
    """
    m = hashlib.md5()
    m.update(value.encode("utf-8"))
    return m.hexdigest()[:8]


def generate_id(prefix: str = "") -> str:
    """Generate an uuid to be used as id.

    :param prefix: Prefix to add to the generated ID.
    """
    return prefix + str(uuid.uuid4())[:8]


def clean_name(name: str):
    """Clean a name for the node, edge.

    Because every name we use is double-quoted, we just have to convert the double quotes to html special char.
    See https://www.graphviz.org/doc/info/lang.html at the bottom of the page.
    :param name: pretty name of the object
    :return: string with double quotes converted to html special char
    """
    return name.strip().replace('"', "&#34;")


def get_play_colors(play_id: str) -> tuple[str, str]:
    """Generate two colors (in hex) for a given play: the main color and the color to use as a font color.

    :param play_id
    :return: The main color and the font color.
    """
    picked_color = Color(pick_for=play_id, luminance=0.4)
    play_font_color = "#ffffff"

    return picked_color.get_hex_l(), play_font_color


def has_role_parent(task_block: Task) -> bool:
    """Check if one of the parents of the task or block is a role.

    :param task_block:
    :return:
    """
    parent = task_block._parent
    while parent:
        if parent._role:
            return True
        parent = parent._parent

    return False


def merge_dicts(dict_1: dict[Any, set], dict_2: dict[Any, set]) -> dict[Any, set]:
    """Merge two dicts by grouping keys and appending values to the set.

    :param dict_1:
    :param dict_2:
    :return:
    """
    final = defaultdict(set)
    # iterate dict items
    all_dict_items = map(methodcaller("items"), [dict_1, dict_2])
    for k, v in chain.from_iterable(all_dict_items):
        final[k].update(v)

    return final


def handle_include_path(
    original_task: TaskInclude,
    loader: DataLoader,
    templar: Templar,
) -> str:
    """Handle relative includes by walking up the list of parent include tasks.

    This function is widely inspired by the static method ansible uses when executing the playbook.
    See :func:`~ansible.playbook.included_file.IncludedFile.process_include_results`

    :param original_task:
    :param loader:
    :param templar:
    :return:
    """
    parent_include = original_task._parent
    include_file = None
    # task path or role name
    include_param = original_task.args.get(
        "_raw_params",
        original_task.args.get("name", None),
    )

    cumulative_path = None
    while parent_include is not None:
        if not isinstance(parent_include, TaskInclude):
            parent_include = parent_include._parent
            continue
        if isinstance(parent_include, IncludeRole):
            parent_include_dir = parent_include._role_path
        else:
            try:
                parent_include_dir = os.path.dirname(
                    templar.template(parent_include.args.get("_raw_params")),
                )
            except AnsibleError as e:
                parent_include_dir = ""
                display.warning(
                    f"Templating the path of the parent {original_task.action} failed. The path to the "
                    "included file may not be found. "
                    f"The error was: {to_text(e)}.",
                )

        if cumulative_path is not None and not os.path.isabs(cumulative_path):
            cumulative_path = os.path.join(parent_include_dir, cumulative_path)
        else:
            cumulative_path = parent_include_dir
        include_target = templar.template(include_param)
        if original_task._role:
            new_basedir = os.path.join(
                original_task._role._role_path,
                "tasks",
                cumulative_path,
            )
            candidates = [
                loader.path_dwim_relative(
                    original_task._role._role_path,
                    "tasks",
                    include_target,
                ),
                loader.path_dwim_relative(new_basedir, "tasks", include_target),
            ]
            for include_file in candidates:
                try:
                    # may throw OSError
                    os.stat(include_file)
                    # or select the task file if it exists
                    break
                except OSError:
                    pass
        else:
            include_file = loader.path_dwim_relative(
                loader.get_basedir(),
                cumulative_path,
                include_target,
            )

        if os.path.exists(include_file):
            break
        else:
            parent_include = parent_include._parent

    if include_file is None:
        if original_task._role:
            include_target = templar.template(include_param)
            include_file = loader.path_dwim_relative(
                original_task._role._role_path,
                "tasks",
                include_target,
            )
        else:
            include_file = loader.path_dwim(templar.template(include_param))

    return include_file
