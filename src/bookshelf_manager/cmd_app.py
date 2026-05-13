import json
import os
import random
import re
import subprocess
import sys
import webbrowser
from atexit import register
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import md5
from pathlib import Path
from shutil import copy, get_terminal_size
from textwrap import dedent, fill
from typing import Literal
from uuid import uuid7

from dacite import from_dict
from llama_index.llms.openai_like import OpenAILike
from markdown import markdown
from platformdirs import user_config_dir
from prompt_toolkit import HTML, prompt
from prompt_toolkit.shortcuts import print_formatted_text as print

from .catalog_manager import Book, Metadata
from .librarian import Config, Librarian
from .search_manager import SearchResult
from .teacher import Teacher
from .utils import defined_classifications, get_resource_path

if sys.platform == "win32":
    import win32api
    import win32con


@dataclass
class AppConfig:
    base_url: str
    api_key: str
    general_model: str


class CmdApp:
    """Defines a library management application that enables the user to control a library via the command-line."""

    def __init__(self):
        config_dir = Path(
            user_config_dir(
                appname="librarian", appauthor="suncloudsmoon", ensure_exists=True
            )
        )
        self.config_file = config_dir / "config.json"
        if self.config_file.exists():
            contents = self.config_file.read_text(encoding="utf-8")
            self.app_config = from_dict(AppConfig, json.loads(contents))
        else:
            print("LLM Acesss Config (config is stored inside the appdata folder):")
            base_url = prompt("Base URL: ", default="http://localhost:1234/v1")
            api_key = prompt("API Key: ", default="dummy_key")
            general_model = prompt("VLM Model (general tasks): ")
            self.app_config = AppConfig(base_url, api_key, general_model)
            self.save_config()

        self.general_client = OpenAILike(
            api_base=self.app_config.base_url,
            api_key=self.app_config.api_key,
            model=self.app_config.general_model,
            is_function_calling_model=True,
            should_use_structured_outputs=True,
            is_chat_model=True,
            max_tokens=2048,
            timeout=180
        )

    def save_config(self):
        contents = json.dumps(asdict(self.app_config))
        self.config_file.write_text(contents, encoding="utf-8")

    def close(self):
        self.save_config()
        if hasattr(self, "librarian"):
            self.librarian.close()

    def create_librarian_dir(self, library_path: Path):
        library_path.mkdir()
        if sys.platform == "win32":
            win32api.SetFileAttributes(
                str(library_path), win32con.FILE_ATTRIBUTE_HIDDEN
            )

    def do_chore(self):
        # Chores
        default_config = Config()
        librarian_path = Path(".librarian")
        if not librarian_path.exists():
            print(
                "Welcome to Librarian, a simple program that helps you make sense of documents."
            )
            self.create_librarian_dir(librarian_path)
            while True:
                try:
                    choices = {
                        "Dewey Decimal Classification (DDC)": "dewey",
                        "Library of Congress (LOC)": "loc",
                        "Universal Decimal Classification (UDC)": "udc",
                    }
                    formatted_choices = "\n".join(
                        f"{num}. {choice}"
                        for num, choice in enumerate(choices, start=1)
                    )
                    answer = (
                        prompt(
                            f"Pick a library classification system:\n{formatted_choices}\n> ",
                            default="1",
                        )
                        .strip()
                        .lower()
                    )
                    index = int(answer) - 1
                    if index >= len(choices):
                        raise ValueError("invalid choice")
                    default_config.classification_system = choices[list(choices)[index]]
                    break
                except Exception as err:
                    print(HTML(f"<ansired>{err}</ansired>"))

            copy(
                get_resource_path(
                    Path(f"data/{default_config.classification_system}.json")
                ),
                librarian_path,
            )

        # Initializing librarian
        self.librarian = Librarian(
            librarian_path=librarian_path,
            default_config=default_config,
            general_client=self.general_client,
        )
        register(self.close)

    def setup(self):
        try:
            self.do_chore()
            self.db_refresh_check()
        except Exception as err:
            # raise
            print(HTML(f"<ansired>{err}</ansired>"))
            os._exit(1)

    # all the commands
    def add(self, query: str, comps: list[str]):
        path = Path(
            query.removeprefix(f"{comps[0]}").strip("\"' ")
        )
        metadata = self.get_metadata_info(filepath=path)
        self.librarian.add(path, metadata)

    def edit(self, comps: list[str], search_results: list[SearchResult] | None = None):
        id = comps[1]
        if id.isnumeric():
            id = search_results[int(id) - 1].id
        book = self.librarian[id][0]
        self.librarian.edit(book)

    def exam(self, comps: list[str]):
        models_info = self.client.models.list().model_dump()
        models = [
            item["id"] for item in models_info["data"] if not "embed" in item["id"]
        ]

        choices = "\n".join([f"{i+1}. {name}" for i, name in enumerate(models)])
        user_input = prompt(f"Pick one of the following models:\n{choices}\n> ")
        model_name = models[int(user_input) - 1]

        teacher = Teacher(
            librarian=self.librarian,
            chat_client=self.client,
            model_name=model_name,
        )

        # Export options
        export_type = (
            prompt(
                "Select the export file format [pdf or text]: ",
                default="pdf",
            )
            .lower()
            .strip()
        )
        if export_type not in ["pdf", "text"]:
            raise NameError(f"incorrect choice {export_type}")
        teacher.export_exam(
            filetype=export_type,
            filepath=Path.home()
            / "Documents"
            / datetime.now().strftime("%Y%m%d-%H%M%S"),
            exam=teacher.create_exam(),
        )

    def fts(self, query: str):
        search_results = self.librarian.search(query, search_type="fts")
        self.print_results(search_results)

    def go(self, comps: list[str], search_results: list[SearchResult]):
        index: int = int(comps[1]) - 1
        result = search_results[index]
        metadata: Metadata = result.metadata
        doc_path: Path = self.librarian.get_document_path(metadata).absolute
        if sys.platform == "win32":
            os.startfile(doc_path)
        elif sys.platform == "darwin":
            applescript = dedent(
                r"""
                on run argv
                set posixPath to item 1 of argv
                set thePage to (item 2 of argv) as integer

                tell application "Adobe Acrobat"
                    activate
                    open (POSIX file posixPath)
                end tell

                delay 0.8

                tell application "System Events"
                    tell process "AdobeAcrobat"
                    keystroke "n" using {shift down, command down}
                    delay 0.2
                    keystroke (thePage as text)
                    keystroke return
                    end tell
                end tell
                end run
                """
            )
            subprocess.run(
                ["osascript", "-", doc_path, str(result.page)],
                applescript,
                text=True,
                check=True,
            )
        elif metadata.filename.lower().endswith("pdf"):
            webbrowser.open(doc_path)

    def info(self, comps: list[str], search_results: list[SearchResult]):
        index: int = int(comps[1]) - 1
        metadata = search_results[index].metadata
        print(metadata)

    def chat(self, comps: list[str]):
        markdown_output = self.librarian.question(" ".join(word for word in comps[1:]))
        if self.librarian.config.exclude_thinking_tag:
            markdown_output = re.sub(
                r"<think>.*</think>",
                "",
                markdown_output,
                flags=re.DOTALL,
            )
        markdown_output = markdown_output.replace("<think>", "&lt;think&gt;").replace(
            "</think>", "&lt;/think&gt;"
        )
        html_output = HTML(markdown(markdown_output))
        print(html_output)
    
    def random(self):
        items = list(self.librarian.catalog_manager)
        item = random.choice(items)
        print(item)

    def remove(self, comps: list[str], search_results: list[str] | None = None):
        id = comps[1]
        if id.isnumeric():
            id = search_results[int(id) - 1].id
        self.librarian.remove(id)

    def stats(self):
        num_books = len(self.librarian.catalog_manager)
        print(f"Total # of Books: {num_books}")

    def sync(self):
        password = prompt("Enter password for encrypted syncing: ").strip()
        mode = prompt("Do you want to start as a server or client? ").strip().lower()
        if mode == "server":
            is_client = False
            server_addr = ("0.0.0.0", 1230)
        elif mode == "client":
            is_client = True
            infos = prompt("Enter server address and port: ").strip()
            components = infos.split(":")
            components[1] = int(components[1])
            server_addr = tuple(components)
        else:
            raise ValueError(f"unknown mode {mode}")

        self.librarian.sync(
            is_client=is_client,
            password=password,
            server_addr=server_addr,
        )

    def help(self):
        commands = {
            ":add [path]": "adds a book to the library from the specified path",
            ":clear": "clears the terminal contents",
            ":edit [id]": "edits the book's metadata",
            ":exam": "uses generative AI to create exams",
            ":fts": "searches the database using full-text search instead of semantic search",
            ":go [#]": "opens the file based on the specified search result number",
            ":help": "displays a list of commands",
            ":info [#]": "shows metadata about a given book identified by the specified search result number",
            ":question [prompt]": "prompts the LLM using context from a standard search",
            ":random": "shows info about a random book sampled from the catalog",
            ":remove [id]": "removes the book identified by the specified ID from the catalog",
            ":stats": "displays all relevant statistics about the library",
            ":sync": "syncs library contents between local LAN devices",
        }
        print("Commands:")
        for command, info in commands.items():
            print(f"  {command:<25} {info.capitalize()}")

    def start(self):
        do_once = True
        if not Path(".librarian").exists():
            self.setup()
            do_once = False

        while True:
            try:
                try:
                    query = prompt(">>> ").strip()
                except KeyboardInterrupt:
                    break
                else:
                    if do_once:
                        self.setup()
                        do_once = False

                if query.startswith(":"):
                    comps = query.split()
                    command = comps[0].lower()
                    match command:
                        case ":add":
                            try:
                                self.add(query, comps)
                            except KeyboardInterrupt:
                                continue
                        case ":clear" | ":cls":
                            os.system("cls" if os.name == "nt" else "clear")
                        case ":edit":
                            try:
                                self.edit(comps, search_results)
                            except KeyboardInterrupt:
                                continue
                        case ":exam":
                            self.exam()
                        case ":fts":
                            self.fts(query)
                        case ":go":
                            self.go(comps, search_results)
                        case ":help":
                            self.help()
                            print()
                        case ":info":
                            self.info(comps, search_results)
                        case ":question" | ":chat" | ":iwonder":
                            self.chat(comps)
                        case ":quit" | ":q" | ":exit" | ":shutdown" | ":logoff":
                            break
                        case ":random":
                            self.random()
                            print()
                        case ":remove" | ":delete":
                            self.remove(comps, search_results)
                        case "stats":
                            self.stats()
                            print()
                        case ":sync":
                            try:
                                self.sync()
                            except KeyboardInterrupt:
                                break
                            else:
                                do_once = (
                                    True  # Refresh the librarian instance after syncing
                                )
                        case _:
                            raise NotImplementedError(f'unknown command: "{command}"')
                    continue

                # Normal semantic search
                search_results = self.librarian.search(query)
                self.print_results(search_results)
            except Exception as err:
                # raise
                print(HTML(f"<ansired>Error: {err}</ansired>"))
            finally:
                self.close()

        print(f"\nHave a nice day, {os.getlogin()}")  # Goodbye

    def slugify(self, isbn: str, title: str, extension: str):
        clean_title = "-".join(title.lower().split())
        slugified = re.sub(r"[^\w-]", "", clean_title)[
            : 255 - len(isbn + extension + "-.")
        ]
        return f"{isbn}-{slugified}.{extension}"

    def is_valid_isbn(self, isbn: str) -> bool:
        """Checks if the given ISBN-13 is valid or not."""
        if len(isbn) != 13:
            return False

        total = sum(
            [int(digit) for digit in range(0, 13, 2)]
            + [int(digit) * 3 for digit in range(1, 13, 2)]
        )
        return total % 10 == 0

    def get_metadata_info(
        self,
        filepath: Path = None,
        metadata: Metadata = None,
    ) -> Metadata:
        if not (filepath or metadata):
            raise ValueError(
                "get_metadata_info() needs either filepath or metadata values"
            )
        if not filepath:
            filepath = self.librarian.get_document_path(book)
        if not metadata:
            if filepath.suffix.lower().endswith(("pdf", "epub", "mobi")):
                metadata_type = "book"
                default_data = Book(
                    isbn="",
                    title="",
                    authors=[],
                    publisher="",
                    series="",
                    edition="",
                    volume="",
                    year=0,
                    url="",
                    description="",
                    notes="",
                )
            metadata = Metadata(
                type=metadata_type,
                id=str(uuid7()),
                hash=md5(filepath.read_bytes()).hexdigest(),
                call_number="",
                filename="",
                data=default_data,
            )
            error_msg = lambda msg: HTML(f"<ansired>{msg}</ansired>")

            while True:
                call_number = prompt("Call Number: ", default=metadata.call_number).strip()
                if len(call_number) > 0:
                    break
                print(error_msg("Call number needs to be specified, try again."))

            book = deepcopy(metadata.data)
            while True:
                isbn = re.sub(r"[^0-9]", "", prompt("ISBN: ", default=book.isbn))
                zero_isbn = "0" * 13
                if len(isbn) == 0:
                    isbn = zero_isbn

                if isbn != zero_isbn and isbn in self.librarian:
                    print(error_msg(f"ISBN {isbn} already exists in the database"))
                elif self.is_valid_isbn(isbn):
                    break
                print(error_msg("Invalid ISBN, try again"))
            book.isbn = isbn

            while True:
                title = prompt("Title: ", default=book.title).strip()
                if len(title) > 0:
                    break
                print(error_msg("Empty title, try again."))
            book.title = title

            while True:
                names = str(book.authors).replace("'", "")[1:-1]
                authors = [
                    author.title().strip()
                    for author in prompt("Authors: ", default=names).split(",")
                ]
                if len(authors[0]) > 0:
                    break
                print(error_msg("Author list is too short, try again."))
            book.authors = authors

            clean = lambda text: text.strip().title()
            book.publisher = clean(prompt("Publisher: ", default=book.publisher))
            book.series = clean(prompt("Series: ", default=book.series))
            book.edition = clean(prompt("Edition: ", default=book.edition))
            book.volume = clean(prompt("Volume: ", default=book.volume))

            while True:
                try:
                    default_year = "" if book.year == 0 else str(book.year)
                    result = prompt("Year: ", default=default_year)
                    book.year = int(result)
                except ValueError:
                    print(error_msg("Invalid year, try again."))
                else:
                    break

            book.url = prompt("URL: ", default=book.url).strip()
            book.description = prompt("Description: ", default=book.description).strip()
            book.notes = prompt("Notes: ", default=book.notes).strip()

            filepath_suffix = filepath.suffix
            filename = self.slugify(book.isbn, title, filepath_suffix[1:])

            metadata.data = book

        return Metadata(
            type=metadata_type,
            id=metadata.id,
            hash=metadata.hash,
            call_number=call_number,
            filename=filename,
            data=metadata.data,
        )

    def print_results(self, results: list[SearchResult]):
        columns = get_terminal_size().columns

        for i, result in enumerate(results):
            print(f"Result {i + 1}")
            print("-" * columns)
            contents = fill(
                result.page_content,
                width=columns,
                initial_indent="  “",
                subsequent_indent="  ",
                max_lines=2,
                placeholder=" [...]”",
            )
            if not contents.endswith("”"):
                contents += "”"
            print(
                contents,
                end="\n\n",
            )

            metadata = result.metadata
            data = metadata.data
            if (mtype := result.metadata.type) == "book":
                book = data
                metadata = {
                    "title": book.title,
                    "authors": str(book.authors)[1:-1].replace("'", ""),
                    "call_number": metadata.call_number,
                    "page": result.page,
                }
            else:
                raise NotImplementedError(f"unknown metadata type {mtype}")

            for name, value in metadata.items():
                formatted_name = name.replace("_", " ").title()
                print(f"  {formatted_name:>{len('call_number')}} : {value}")
            print()

    def db_refresh_check(self):
        if self.librarian.is_database_mismatch():
            print(
                "There is a mismatch between the catalog manager and the vector database records."
            )
            choice = (
                prompt("🔃 A full database refresh is recommended. Proceed? (y/n) ")
                .strip()
                .lower()
            )
            if choice == "y" or choice == "yes" or choice == "ok":
                self.librarian.refresh()
