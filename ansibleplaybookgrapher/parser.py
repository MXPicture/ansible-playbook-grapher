# Copyright (C) 2025 Mohamed El Mouctar HAIDARA
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
from abc import ABC, abstractmethod
from types import NoneType

from ansible.cli import CLI
from ansible.errors import AnsibleError, AnsibleParserError, AnsibleUndefinedVariable
from ansible.parsing.yaml.objects import AnsibleSequence, AnsibleUnicode
from ansible.playbook import Playbook
from ansible.playbook.block import Block
from ansible.playbook.handler import Handler
from ansible.playbook.helpers import load_list_of_blocks
from ansible.playbook.play import Play
from ansible.playbook.role import Role
from ansible.playbook.role_include import IncludeRole
from ansible.playbook.task import Task
from ansible.playbook.task_include import TaskInclude
from ansible.template import Templar
from ansible.utils.display import Display

from ansibleplaybookgrapher.graph_model import (
    BlockNode,
    CompositeNode,
    HandlerNode,
    PlaybookNode,
    PlayNode,
    RoleNode,
    TaskNode,
)
from ansibleplaybookgrapher.utils import (
    clean_name,
    convert_when_to_str,
    generate_id,
    handle_include_path,
    has_role_parent,
    hash_value,
)

display = Display()


class BaseParser(ABC):
    """Base Parser of a playbook."""

    def __init__(
        self,
        tags: list[str] | None = None,
        skip_tags: list[str] | None = None,
    ) -> None:
        """

        :param tags: Only add plays and tasks tagged with these values
        :param skip_tags: Only add plays and tasks whose tags do not match these values
        """
        loader, inventory, variable_manager = CLI._play_prereqs()
        self.data_loader = loader
        self.inventory_manager = inventory
        self.variable_manager = variable_manager

        self.tags = tags or ["all"]
        self.skip_tags = skip_tags or []

    @abstractmethod
    def parse(self, *args, **kwargs) -> PlaybookNode:
        pass

    def template(
        self,
        data: str | AnsibleUnicode,
        variables: dict,
        fail_on_undefined: bool = False,
    ) -> str | AnsibleUnicode:
        """Template the data using Jinja. Return data if an error occurs during the templating
        :param data:
        :param fail_on_undefined:
        :param variables:
        :return:
        """
        try:
            templar = Templar(loader=self.data_loader, variables=variables)
            return templar.template(data, fail_on_undefined=fail_on_undefined)
        except AnsibleError as ansible_error:
            # Sometimes we need to export
            if fail_on_undefined:
                raise
            display.warning(ansible_error.message)
            return data

    def _add_task(
        self,
        task: Task,
        task_vars: dict,
        node_type: str,
        play_node: PlayNode,
        parent_node: CompositeNode,
    ) -> bool:
        """Add the task in the graph.

        :param task: The task to include.
        :param task_vars: The variables of the task.
        :param node_type: The type of the node.
        :param parent_node: The parent node.
        :return: True if the task has been included, false otherwise.
        """
        # Ansible-core 2.11 added an implicit meta-task at the end of the role. So wee skip it here.
        if task.action == "meta" and task.implicit:
            return False

        if not task.evaluate_tags(
            only_tags=self.tags,
            skip_tags=self.skip_tags,
            all_vars=task_vars,
        ):
            display.vv(f"The task '{task.get_name()}' is skipped due to the tags.")
            return False

        display.vv(f"Adding the {node_type} '{task.get_name()}' to the graph")

        task_name = clean_name(self.template(task.get_name(), task_vars))
        node_params = {
            "node_name": task_name,
            "node_id": generate_id(f"{node_type}_"),
            "when": convert_when_to_str(task.when),
            "raw_object": task,
            "notify": _get_notified_handlers(task),
        }

        if node_type == "handler":
            # If we are here, the task is a Handler
            task: Handler
            # It seems like the 'listen' attribute is not available in some cases. There might be a bug somewhere. For now, I avoid the error.
            # See https://github.com/haidaraM/ansible-playbook-grapher/issues/243
            task_listen = getattr(task, "listen", [])
            if isinstance(task_listen, list):
                listen = task_listen
            else:
                listen = [task_listen]

            node = HandlerNode(**node_params, listen=listen)

            # We make the Handlers defined in the roles are available at the play level
            if has_role_parent(task):
                play_node.add_node(
                    target_composition="handlers",
                    node=node,
                )
        else:
            node = TaskNode(**node_params)

        parent_node.add_node(
            target_composition=f"{node_type}s",
            node=node,
        )

        return True


def _get_notified_handlers(task: Task) -> list[str]:
    """Get the handlers that are notified by the task.

    :param task: The task to get the notified from.
    :return:
    """
    handlers = []
    if task.notify:
        if isinstance(task.notify, AnsibleUnicode):
            handlers.append(task.notify)
        elif isinstance(task.notify, AnsibleSequence):
            handlers.extend(task.notify)

    return handlers


class PlaybookParser(BaseParser):
    """The playbook parser. This is the main entrypoint responsible to parser the playbook into a graph structure."""

    def __init__(
        self,
        playbook_path: str,
        tags: list[str] | None = None,
        skip_tags: list[str] | None = None,
        group_roles_by_name: bool = False,
        playbook_name: str | None = None,
        exclude_roles: list[str] | None = None,
    ) -> None:
        """

        :param playbook_path: The path of the playbook to parse.
        :param tags: Only add plays and tasks tagged with these values.
        :param skip_tags: Only add plays and tasks whose tags do not match these values.
        :param group_roles_by_name: Group roles by name instead of considering them as separate nodes with different IDs.
        :param playbook_name: On optional name of the playbook to parse.
        :param exclude_roles: Only add tasks whose roles do not match these values
        It will be used as the node name if provided in replacement of the file name.
        """
        super().__init__(tags=tags, skip_tags=skip_tags)
        self.group_roles_by_name = group_roles_by_name
        self.playbook_path = playbook_path
        self.playbook_name = playbook_name
        self.exclude_roles = exclude_roles or []

    def parse(self, *args, **kwargs) -> PlaybookNode:
        """Loop through the playbook and generate the graph.

        The graph is parsed following this order (https://docs.ansible.com/ansible/2.4/playbooks_reuse_roles.html#using-roles)
        for each play:
            add pre_tasks
            add roles
                add role tasks
                add role handlers
            add tasks
            add post_tasks
            add handlers
        :return:
        """
        display.display(f"Parsing the playbook '{self.playbook_path}'")
        playbook = Playbook.load(
            self.playbook_path,
            loader=self.data_loader,
            variable_manager=self.variable_manager,
        )
        # the root node
        playbook_node_name = (
            self.playbook_name if self.playbook_name else self.playbook_path
        )
        playbook_root_node = PlaybookNode(playbook_node_name, raw_object=playbook)
        # loop through the plays
        for play in playbook.get_plays():
            # the load basedir is relative to the playbook path
            if play._included_path is not None:
                self.data_loader.set_basedir(play._included_path)
            else:
                self.data_loader.set_basedir(playbook._basedir)
            display.vvv(f"Loader basedir set to {self.data_loader.get_basedir()}")

            play_vars = self.variable_manager.get_vars(play)
            play_hosts = [
                h.get_name()
                for h in self.inventory_manager.get_hosts(
                    self.template(play.hosts, play_vars),
                )
            ]
            play_name = self.template(clean_name(play.get_name()), play_vars)

            display.v(f"Parsing {play_name}")

            play_node = PlayNode(
                play_name,
                hosts=play_hosts,
                raw_object=play,
            )
            playbook_root_node.add_node("plays", play_node)

            # loop through the pre_tasks
            display.v("Parsing pre_tasks...")
            for pre_task_block in play.pre_tasks:
                self._include_tasks_in_blocks(
                    current_play=play,
                    current_play_node=play_node,
                    parent_nodes=[play_node],
                    block=pre_task_block,
                    play_vars=play_vars,
                    node_type="pre_task",
                )

            # loop through the roles
            display.v("Parsing roles...")

            for role in play.get_roles():  # type: Role
                # Don't insert tasks from ``import/include_role``, preventing duplicate graphing
                if role.from_include:
                    continue

                # Don't show roles, which are set in --exclude-roles option
                if role.get_name() in self.exclude_roles:
                    continue

                if self.group_roles_by_name:
                    # If we are grouping roles, we use the hash of role name as the node id
                    role_node_id = "role_" + hash_value(role.get_name())
                else:
                    # Otherwise, a random id is used
                    role_node_id = generate_id("role_")
                role_node = RoleNode(
                    clean_name(role.get_name()),
                    node_id=role_node_id,
                    raw_object=role,
                )
                # edge from play to role
                play_node.add_node("roles", role_node)

                # loop through the tasks of the roles
                for block in role.compile(play):
                    self._include_tasks_in_blocks(
                        current_play=play,
                        current_play_node=play_node,
                        parent_nodes=[role_node],
                        block=block,
                        play_vars=play_vars,
                        node_type="task",
                    )

                # loop through the handlers of the roles
                for handler_block in role.get_handler_blocks(play):
                    self._include_tasks_in_blocks(
                        current_play=play,
                        current_play_node=play_node,
                        parent_nodes=[role_node],
                        block=handler_block,
                        play_vars=play_vars,
                        node_type="handler",
                    )
            # end of the roles loop

            # loop through the tasks
            display.v("Parsing tasks...")
            for task_block in play.tasks:
                self._include_tasks_in_blocks(
                    current_play=play,
                    current_play_node=play_node,
                    parent_nodes=[play_node],
                    block=task_block,
                    play_vars=play_vars,
                    node_type="task",
                )

            # loop through the post_tasks
            display.v("Parsing post_tasks...")
            for post_task_block in play.post_tasks:
                self._include_tasks_in_blocks(
                    current_play=play,
                    current_play_node=play_node,
                    parent_nodes=[play_node],
                    block=post_task_block,
                    play_vars=play_vars,
                    node_type="post_task",
                )

            # loop through the handlers of the play
            for handler_block in play.get_handlers():
                self._include_tasks_in_blocks(
                    current_play=play,
                    current_play_node=play_node,
                    parent_nodes=[play_node],
                    block=handler_block,
                    play_vars=play_vars,
                    node_type="handler",
                )

            # Summary
            display.v(f"{len(play_node.pre_tasks)} pre_task(s) added to the graph.")
            display.v(f"{len(play_node.roles)} role(s) added to the play")
            display.v(f"{len(play_node.tasks)} task(s) added to the play")
            display.v(f"{len(play_node.post_tasks)} post_task(s) added to the play")
            display.v(f"{len(play_node.handlers)} handlers(s) added to the play")
            # moving to the next play

        playbook_root_node.calculate_indices()
        return playbook_root_node

    def _include_tasks_in_blocks(
        self,
        current_play: Play,
        current_play_node: PlayNode,
        parent_nodes: list[CompositeNode],
        block: Block | TaskInclude,
        node_type: str,
        play_vars: dict,
    ) -> None:
        """Recursively read all the tasks of the block and add it to the graph.

        :param parent_nodes: This is the list of parent nodes. Each time, we see an include_role, the corresponding node is
        added to this list
        :param current_play: The current Ansible play, which the tasks are included.
        :param current_play_node: The current play node, which the tasks are included.
        :param block: The current block to include.
        :param play_vars: The variables of the play.
        :param node_type: The type of the node. It can be a task, a pre_task, a post_task or a handler.
        :return:
        """
        if Block.is_block(block.get_ds()):
            # Here we have an explicit block. Ansible internally converts all normal tasks to Block
            block_node = BlockNode(
                str(block.name),
                when=convert_when_to_str(block.when),
                raw_object=block,
            )
            parent_nodes[-1].add_node(f"{node_type}s", block_node)
            parent_nodes.append(block_node)

        # loop through the tasks
        for task_or_block in block.block:
            if hasattr(task_or_block, "loop") and task_or_block.loop:
                display.warning(
                    "Looping on tasks or roles are not supported for the moment. Only the task having the loop argument will be added to the graph.",
                )

            if isinstance(task_or_block, Block):
                self._include_tasks_in_blocks(
                    current_play=current_play,
                    current_play_node=current_play_node,
                    parent_nodes=parent_nodes,
                    block=task_or_block,
                    node_type=node_type,
                    play_vars=play_vars,
                )
            elif isinstance(
                task_or_block,
                TaskInclude,
            ):  # include, include_tasks, include_role are dynamic
                # So we need to process them explicitly because Ansible does it during the execution of the playbook

                task_vars = self.variable_manager.get_vars(
                    play=current_play,
                    task=task_or_block,
                )

                if isinstance(task_or_block, IncludeRole):
                    # Here we have an 'include_role'. The class IncludeRole is a subclass of TaskInclude.
                    # We do this because the management of an 'include_role' is different.
                    # See :func:`~ansible.playbook.included_file.IncludedFile.process_include_results` from line 155
                    display.v(f"An 'include_role' found: '{task_or_block.get_name()}'")

                    # Don't show roles, which are set in --exclude-roles option
                    if task_or_block._role_name in self.exclude_roles:
                        continue

                    if not task_or_block.evaluate_tags(
                        only_tags=self.tags,
                        skip_tags=self.skip_tags,
                        all_vars=task_vars,
                    ):
                        display.vv(
                            f"The include_role '{task_or_block.get_name()}' is skipped due to the tags.",
                        )
                        continue  # Go to the next task

                    # Here we are using the role name instead of the task name to keep the same behavior  as a
                    #  traditional role
                    if self.group_roles_by_name:
                        # If we are grouping roles, we use the hash of role name as the node id
                        role_node_id = "role_" + hash_value(task_or_block._role_name)
                    else:
                        # Otherwise, a random id is used
                        role_node_id = generate_id("role_")
                    role_node = RoleNode(
                        task_or_block._role_name,
                        node_id=role_node_id,
                        when=convert_when_to_str(task_or_block.when),
                        raw_object=task_or_block,
                        include_role=True,
                    )
                    parent_nodes[-1].add_node(
                        f"{node_type}s",
                        role_node,
                    )

                    if (
                        task_or_block.loop
                    ):  # Looping on include_role is not supported yet
                        continue  # Go the next task
                    else:
                        # If we have an include_role, and we want to include its tasks, the parent node now becomes
                        # the role.
                        parent_nodes.append(role_node)
                        block_list, _ = task_or_block.get_block_list(
                            play=current_play,
                            loader=self.data_loader,
                            variable_manager=self.variable_manager,
                        )

                else:
                    display.v(
                        f"An 'include_tasks' found. Including tasks from '{task_or_block.get_name()}'",
                    )

                    templar = Templar(loader=self.data_loader, variables=task_vars)
                    try:
                        included_file_path = handle_include_path(
                            original_task=task_or_block,
                            loader=self.data_loader,
                            templar=templar,
                        )
                    except AnsibleUndefinedVariable as e:
                        # TODO: mark this task with some special shape or color
                        display.warning(
                            f"Unable to translate the include task '{task_or_block.get_name()}' due to an undefined variable: {e!s}. "
                            "Some variables are available only during the execution of the playbook.",
                        )
                        self._add_task(
                            play_node=current_play_node,
                            task=task_or_block,
                            task_vars=task_vars,
                            node_type=node_type,
                            parent_node=parent_nodes[-1],
                        )
                        continue

                    data = self.data_loader.load_from_file(included_file_path)
                    if data is None:
                        display.warning(
                            f"The file '{included_file_path}' is empty and has no tasks to include",
                        )
                        continue
                    elif not isinstance(data, list):
                        msg = "Included task files must contain a list of tasks"
                        raise AnsibleParserError(
                            msg,
                            obj=data,
                        )

                    # get the blocks from the include_tasks
                    block_list = load_list_of_blocks(
                        data,
                        play=current_play,
                        variable_manager=self.variable_manager,
                        role=task_or_block._role,
                        loader=self.data_loader,
                        parent_block=task_or_block,
                    )

                for b in (
                    block_list
                ):  # loop through the blocks inside the included tasks or role
                    self._include_tasks_in_blocks(
                        current_play=current_play,
                        current_play_node=current_play_node,
                        parent_nodes=parent_nodes,
                        block=b,
                        play_vars=task_vars,
                        node_type=node_type,
                    )
                if isinstance(task_or_block, IncludeRole) and len(parent_nodes) > 1:
                    # We remove the parent node we have added if we included some tasks from a role
                    parent_nodes.pop()
            else:  # It's here that we add the task in the graph
                if (
                    len(parent_nodes) > 1  # 1
                    and not has_role_parent(task_or_block)  # 2
                    and parent_nodes[-1].raw_object != task_or_block._parent  # 3
                ):
                    # We remove a parent node:
                    # 1. When have at least two parents. Every node (except the playbook) should have a parent node
                    #   AND
                    # 2. The current node doesn't have a role as parent
                    #   AND
                    # 3. The last parent node is different from the current node parent. This means that we are
                    #   done with the child nodes of this parent node
                    parent_nodes.pop()

                self._add_task(
                    play_node=current_play_node,
                    task=task_or_block,
                    task_vars=play_vars,
                    node_type=node_type,
                    parent_node=parent_nodes[-1],
                )
