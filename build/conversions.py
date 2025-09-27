import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4
import subprocess
from shutil import copy, move
from huggingface_hub import HfFileSystem

sys.path.append(".")
from utils import Category, ClassificationSystem
from catalog_manager import Book


# model conversion time
def dump_onnx_model(huggingface_model: str, working_dir: str):
    comps = huggingface_model.split("/")
    company = comps[0].lower()
    model = comps[1].lower()

    output_dir = Path(f"{working_dir}/models/{company}")
    model_dir = output_dir / model
    if model_dir.exists():
        return

    args = [
        "olive",
        "auto-opt",
        "--model_name_or_path",
        huggingface_model,
        "--device",
        "gpu",
        "--provider",
        "DmlExecutionProvider",
        "--use_model_builder",
        "--precision",
        "int4",
        "--output_path",
        output_dir,
    ]
    subprocess.run(args)

    src_path = output_dir / "model"
    os.rename(src_path, model_dir)
    move(output_dir / "model_config.json", model_dir)

    # Copy chat template to model directory
    chat_template = f"{working_dir}/chat_templates/{company}/{model.lower()}.json"
    copy(chat_template, model_dir / "inference_model.json")

    # Create LICENSE if it exists
    fs = HfFileSystem()
    files = fs.glob(f"{huggingface_model}/LICENSE")
    if files:
        contents = fs.read_text(files[0], encoding="utf-8")
        Path(model_dir / "LICENSE.txt").write_text(contents, encoding="utf-8")


def dump_foundry_local(out_dir: str):
    path = Path(f"{out_dir}/deps/foundry_local/")
    if not path.exists() or not list(path.glob("*.msix")):
        path.mkdir(parents=True, exist_ok=True)
        args = [
            "winget",
            "download",
            "--id",
            "Microsoft.FoundryLocal",
            "--version",
            "0.6.87.59034",
            "--architecture",
            "x64",
            "--download-directory",
            path,
        ]
        subprocess.run(args)


# other stuff
def dump_class_results(path: str, type: str, data: list[Category]):
    system = ClassificationSystem(type)
    system.data_list = data
    contents = json.dumps(system.dumps(), indent=4, ensure_ascii=False)
    Path(f"{path}/{type}.json").write_text(contents, encoding="utf-8")


def __process_dewey(lines: list[str], step=100):
    infos = []
    for i in range(0, len(lines), step):
        code, name = lines[i].split(maxsplit=1)
        if step != 1:
            info = Category(
                code, name, __process_dewey(lines[i : i + step], step // 10)
            )
        else:
            info = Category(code, name)
        infos.append(info)
    return infos


def create_dewey_system(dest_dir: str, data_dir: str):
    paths = ["main_classes.txt", "hundred_divisions.txt", "thousand_sections.txt"]
    deweys = {}
    for i, path in enumerate(paths):
        contents = Path(f"{data_dir}/{path}").read_text(encoding="utf-8").splitlines()
        deweys[path] = __process_dewey(contents, step=10**i)

    top_dewey = deweys[paths[0]]
    middle_dewey = deweys[paths[1]]
    lower_dewey = deweys[paths[2]]

    for i, level in enumerate(middle_dewey):
        for j in range(len(level.children)):
            level.children[j].children = lower_dewey[i].children[j].children
        top_dewey[i].children = level.children

    dump_class_results(dest_dir, "dewey", top_dewey)


def create_lcc_system(dest_dir: str, data_dir: str):
    contents = Path(f"{data_dir}/subjects.txt").read_text(encoding="utf-8")
    lines = contents.splitlines()
    lcc_list: list[Category] = []
    for line in lines:
        if len(line) == 0:
            continue
        comps = line.split(maxsplit=2)
        category = Category(code=comps[1], name=comps[2])
        if comps[0] == "Subclass":
            lcc_list[-1].children.append(category)
        elif comps[0] == "Class":
            lcc_list.append(category)
        else:
            raise ValueError(f"unknown category {comps[0]}")

    dump_class_results(dest_dir, "lcc", lcc_list)


def create_classification_system(dest_dir: str, data_dir: str, type: str):
    systems = {"dewey": create_dewey_system, "lcc": create_lcc_system}
    systems[type](dest_dir, data_dir)


def convert_old_catalog(catalog_path):
    contents: str = Path(catalog_path).read_text(encoding="utf-8")
    old_catalog: dict = json.loads(contents)
    new_catalog: list[dict] = []
    for metadata in old_catalog.values():
        new_catalog.append(
            asdict(
                Book(
                    id=str(uuid4()),
                    filename=metadata["FileName"],
                    isbn=metadata["ISBN"],
                    call_number=metadata["DeweyNumber"],
                    title=metadata["Title"],
                    authors=[
                        author.title().strip()
                        for author in metadata["Author"].split(",")
                    ],
                    publisher=metadata["Publisher"],
                    series=metadata["Series"],
                    edition=metadata["Edition"],
                    volume=metadata["Volume"],
                    year=int(metadata["Year"]),
                    url=metadata["URL"],
                    description=metadata["Description"],
                    notes="",
                )
            )
        )
    contents: str = json.dumps(new_catalog, indent=4, ensure_ascii=False)
    Path(catalog_path).write_text(contents, encoding="utf-8")
    print("⚒️  Sucessfully converted catalog")


def create_legal(
    legal_path: str,
    notice_path: str,
    search_paths: list[str] = [".venv"],
):
    legal, notice = "", ""

    patterns = [
        "license*",
        "copying*",
        "notice*",
    ]
    for search_path in search_paths:
        search_path = Path(search_path)
        paths = []
        for pattern in patterns:
            paths.extend(search_path.rglob(pattern, case_sensitive=False))

        for path in paths:
            if not path.is_file():
                continue
            contents = path.read_text(encoding="utf-8")
            filler = f"\n\n{"-" * 80}\n\n"
            if path.name.startswith("NOTICE"):
                notice += contents + filler
            else:
                legal += contents + filler

    # Write to files
    infos = {
        legal_path: legal,
        notice_path: notice,
    }
    for path, contents in infos.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        Path(path).write_text(contents, encoding="utf-8")


# convert_old_catalog("library-catalog.json")
