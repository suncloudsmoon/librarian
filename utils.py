"""Contains utilies necessary for the librarian to function."""

import json
import os
from dataclasses import asdict, dataclass, field

from dacite import from_dict

defined_classifications = ["dewey", "lcc"]


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


def create_classification_cls(type: str, text: str):
    systems = {"dewey": DeweyClassificationSystem, "lcc": LCCClassificationSystem}
    return systems[type](text)


def get_resource_path(path):
    app_dir = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(app_dir, path)
