"""Contains utilies necessary for the librarian to function."""

import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dacite import from_dict
from llama_index.core.llms import ChatMessage, ImageBlock, TextBlock
from llama_index.llms.openai_like import OpenAILike

defined_classifications = ["dewey", "lcc", "udc"]


def ask_image(llm: OpenAILike, system_prompt: str, response_format, image_data: str):
    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(
            role="user", blocks=[ImageBlock(url=f"data:image/jpeg;base64,{image_data}")]
        ),
    ]
    response = llm.as_structured_llm(response_format).chat(messages)
    return response.raw


@dataclass
class Category:
    code: str
    name: str
    children: list["Category"] = field(default_factory=list)


class ClassificationSystem:

    def __init__(self, type: str):
        self.type = type

    def get_path(
        self, call_number: str, data_list: list[Category] = None, depth: int = 0
    ) -> str:
        raise NotImplementedError(
            "this is an abstract method, it needs to be implemented in the child class"
        )

    def loads(self, text: str):
        """Loads from data (string) into type and info."""
        values = json.loads(text)
        type, data = values["type"], values["data"]
        if type != self.type:
            raise ValueError(
                f"Cannot load from data because {type} is different from {self.type}"
            )
        self.data_list = [from_dict(Category, info) for info in data]

    def dumps(self):
        """Dumps the current instance data into a dictionary."""
        return {
            "type": self.type,
            "data": [asdict(item) for item in self.data_list],
        }


class DeweyClassificationSystem(ClassificationSystem):

    def __init__(self, text: str):
        super().__init__("dewey")
        self.loads(text)

    def get_path(
        self, call_number: str, data_list: list[Category] = None, depth: int = 0
    ) -> str:
        if data_list is None:
            data_list = self.data_list
        for level in data_list:
            code, name, children = level.code, level.name, level.children
            if depth >= len(code):
                break

            dirname = f"{code} {name}"
            if code == call_number and depth == len(call_number) - 1:
                return dirname
            elif code[depth] == call_number[depth] and children:
                return os.path.join(
                    dirname,
                    self.get_path(call_number, children, depth + 1),
                )
        raise LookupError(f"could not find {call_number}")


class LCCClassificationSystem(ClassificationSystem):

    def __init__(self, text: str):
        super().__init__("lcc")
        self.loads(text)

    def get_path(
        self, call_number: str, data_list: list[Category] = None, depth: int = 0
    ) -> str:
        subject = call_number[:2]
        if data_list is None:
            data_list = self.data_list
        for level in data_list:
            code, name, children = level.code, level.name, level.children
            if depth >= 2:
                break

            dirname = f"{code} {name}"
            code_depth = code[depth] if len(code) > depth else code[-1]
            if subject == code and depth == len(subject) - 1:
                return dirname
            elif code_depth == subject[depth] and children:
                return os.path.join(
                    dirname, self.get_path(call_number, children, depth + 1)
                )
        raise LookupError(f"could not find {call_number}")


class UDCClassificationSystem(ClassificationSystem):

    def __init__(self, text: str):
        super().__init__("udc")
        self.loads(text)

    def get_path(
        self, call_number: str, data_list: list[Category] = None, depth: int = 0
    ) -> str:
        raise NotImplementedError("udc not implemented yet")


def create_classification_cls(type: str, text: str):
    systems = {"dewey": DeweyClassificationSystem, "lcc": LCCClassificationSystem}
    return systems[type](text)


def get_resource_path(path: Path) -> Path:
    app_dir = Path(os.path.dirname(__file__)).absolute()
    return app_dir / path


class Git:

    def __init__(self, work_dir: Path, git_folder: Path):
        self.work_dir = work_dir
        self.git_folder = git_folder
        if not self.git_folder.exists():
            self.initialize()

    def initialize(self):
        subprocess.call(
            args=[
                "git",
                "-C",
                self.work_dir.absolute(),
                "--git-dir",
                self.git_folder.absolute(),
                "init",
            ],
            stdout=open(os.devnull, "wb"),
        )

    def stage(self, exclude_paths: list | None = None):
        args = ["git", "-C", self.work_dir.absolute(), "add", "--", "."] + [
            f":!{path}" for path in exclude_paths
        ]
        subprocess.call(args=args, stdout=open(os.devnull, "wb"))

    def commit(self, message: str):
        subprocess.call(
            args=["git", "-C", self.work_dir.absolute(), "commit", "-m", message],
            stdout=open(os.devnull, "wb"),
        )
