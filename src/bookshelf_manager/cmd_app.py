import json
import warnings
import os

# Suppress distracting warnings
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

import re
import socket
import sys
import webbrowser
from atexit import register
from pathlib import Path
from shutil import copy, get_terminal_size
from textwrap import fill
from uuid import uuid4

from markdown import markdown
from prompt_toolkit import HTML, prompt
from prompt_toolkit.shortcuts import print_formatted_text as print

from .catalog_manager import Book
from .librarian import Config, Librarian
from .search_manager import SearchResult
from .utils import defined_classifications, get_resource_path

import fitz
from io import BytesIO
import warnings
from .teacher import Teacher
from openai import OpenAI
from datetime import datetime
from platformdirs import user_config_dir
from dataclasses import dataclass, asdict
from dacite import from_dict

if sys.platform == "win32":
    import win32api
    import win32con


@dataclass
class AppConfig:
    base_url: str = "http://localhost:1234/v1"
    api_key: str = "dummy_key"


class CmdApp:
    """Defines a library management application that enables the user to control a library via the command-line."""

    def __init__(self):
        config_dir = Path(
            user_config_dir(
                appname="librarian", appauthor="suncloudsmoon", ensure_exists=True
            )
        )
        self.config_file = config_dir / "app_config.json"
        if self.config_file.exists():
            contents = self.config_file.read_text(encoding="utf-8")
            self.app_config = from_dict(AppConfig, json.loads(contents))
        else:
            self.app_config = AppConfig()

    def close(self):
        contents = json.dumps(asdict(self.app_config))
        self.config_file.write_text(contents, encoding="utf-8")

    def create_librarian_dir(self, library_path):
        os.mkdir(library_path)
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
                    answer = (
                        prompt(
                            "The default classification system is Dewey Decimal Classification (DDC), do you want to change it? (y/n) "
                        )
                        .strip()
                        .lower()
                    )
                    if answer == "y":
                        question = f"Choose one of the following\n"
                        for system in defined_classifications:
                            question += f"  {system}\n"
                        default_config.classification_system = (
                            prompt(question).strip().lower()
                        )
                    elif answer != "n":
                        raise ValueError(f"invalid input {answer}")
                    break
                except Exception as err:
                    print(HTML(f"<ansired>{err}</ansired>"))

            copy(
                get_resource_path(f"data/{default_config.classification_system}.json"),
                librarian_path,
            )

        # Initializing librarian
        self.librarian = Librarian(librarian_path, default_config)
        register(self.cleanup)

    def setup(self):
        try:
            self.do_chore()
            self.db_refresh_check()
        except Exception as err:
            print(HTML(f"<ansired>{err}</ansired>"))
            os._exit(1)

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
                            path = Path(
                                query.removeprefix(comps[0]).lstrip().replace('"', "")
                            )
                            try:
                                book, cover_image = self.get_book_info(filepath=path)
                            except KeyboardInterrupt:
                                continue
                            self.librarian.add(path, book, cover_image)
                        case ":clear" | ":cls":
                            os.system("cls" if os.name == "nt" else "clear")
                        case ":edit":
                            id = comps[1]
                            if id.isnumeric():
                                id = search_results[int(id) - 1].id
                            book = self.librarian.info(id)[0]
                            try:
                                book, cover_image = self.get_book_info(book=book)
                            except KeyboardInterrupt:
                                continue
                            else:
                                self.librarian.edit(book)
                        case ":exam":
                            client = OpenAI(
                                base_url=self.app_config.base_url,
                                api_key=self.app_config.api_key,
                            )
                            models_info = client.models.list().model_dump()
                            models = [
                                item["id"]
                                for item in models_info["data"]
                                if not "embed" in item["id"]
                            ]

                            choices = "\n".join(
                                [f"{i+1}. {name}" for i, name in enumerate(models)]
                            )
                            user_input = prompt(
                                f"Pick one of the following models:\n{choices}\n> "
                            )
                            model_name = models[int(user_input) - 1]

                            teacher = Teacher(
                                librarian=self.librarian,
                                chat_client=client,
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
                        case ":go":
                            index: int = int(comps[1]) - 1
                            book: Book = search_results[index].get_book()
                            book_path: str = os.path.abspath(
                                self.librarian.get_book_path(book)
                            )
                            if sys.platform == "win32":
                                os.startfile(book_path)
                            elif book.filename.lower().endswith("pdf"):
                                webbrowser.open(book_path)
                            else:
                                raise NotImplementedError(
                                    "opening files from non-Windows OSs is not supported yet"
                                )
                        case ":help":
                            self.print_help()
                            print()
                        case ":info":
                            index: int = int(comps[1]) - 1
                            book: Book = search_results[index].get_book()
                            print(book)
                        case ":question" | ":chat" | ":iwonder":
                            markdown_output = self.librarian.question(
                                " ".join(word for word in comps[1:])
                            )
                            if self.librarian.config.exclude_thinking_tag:
                                markdown_output = re.sub(
                                    r"<think>.*</think>",
                                    "",
                                    markdown_output,
                                    flags=re.DOTALL,
                                )
                            markdown_output = markdown_output.replace(
                                "<think>", "&lt;think&gt;"
                            ).replace("</think>", "&lt;/think&gt;")
                            html_output = HTML(markdown(markdown_output))
                            print(html_output)
                        case ":quit" | ":q" | ":exit":
                            break
                        case ":remove" | ":delete":
                            id = comps[1]
                            if id.isnumeric():
                                id = search_results[int(id) - 1].id
                            self.librarian.remove(id)
                        case ":sync":
                            password = prompt(
                                "Enter password for encrypted syncing: "
                            ).strip()
                            mode = (
                                prompt("Do you want to start as a server or client? ")
                                .strip()
                                .lower()
                            )
                            if mode == "server":
                                is_client = False
                                server_addr = ("0.0.0.0", 1230)
                            elif mode == "client":
                                is_client = True
                                infos = prompt(
                                    "Enter server address and port: "
                                ).strip()
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
                        case _:
                            raise ValueError(f'unknown command: "{command}"')
                    continue

                search_results = self.librarian.search(query)
                self.print_results(search_results)
            except Exception as err:
                # raise
                print(HTML(f"<ansired>Error: {err}</ansired>"))
            finally:
                self.close()

        # Goodbye
        user = os.getlogin()
        print(f"\nHave a nice day, {user}")

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

    def get_book_info(self, filepath: Path = None, book: Book = None) -> tuple:
        if not (filepath or book):
            raise ValueError("get_book_info() needs either filepath or book values")
        if not filepath:
            filepath = self.librarian.get_book_path(book)
        if not book:
            book = Book(
                id=str(uuid4()),
                filename="",
                isbn="",
                call_number="",
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

        error = lambda msg: HTML(f"<ansired>{msg}</ansired>")
        while True:
            isbn = re.sub(r"[^0-9]", "", prompt("ISBN: ", default=book.isbn))
            if len(isbn) == 0:
                isbn = "0" * 13
            if self.librarian.exists(isbn):
                print(error(f"ISBN {isbn} already exists in the database, try again."))
            if self.is_valid_isbn(isbn):
                break
            print(error("Invalid ISBN, try again."))

        while True:
            call_number = prompt("Call Number: ", default=book.call_number).strip()
            if len(call_number) > 0:
                break
            print(error("Call number needs to be specified, try again."))

        while True:
            title = prompt("Title: ", default=book.title).strip()
            if len(title) > 0:
                break
            print(error("Empty title, try again."))

        while True:
            names = str(book.authors).replace("'", "")[1:-1]
            authors = [
                author.title().strip()
                for author in prompt("Authors: ", default=names).split(",")
            ]
            if len(authors[0]) > 0:
                break
            print(error("Author list is too short, try again."))

        clean = lambda text: text.strip().title()
        publisher = clean(prompt("Publisher: ", default=book.publisher))
        series = clean(prompt("Series: ", default=book.series))
        edition = clean(prompt("Edition: ", default=book.edition))
        volume = clean(prompt("Volume: ", default=book.volume))

        while True:
            try:
                default_year = "" if book.year == 0 else str(book.year)
                result = prompt("Year: ", default=default_year)
                year = int(result)
            except ValueError:
                print(error("Invalid year, try again."))
            else:
                break

        url = prompt("URL: ", default=book.url).strip()
        description = prompt("Description: ", default=book.description).strip()
        notes = prompt("Notes: ", default=book.notes).strip()

        filepath_suffix = filepath.suffix
        if filepath_suffix.lower() == ".pdf":
            pdf = fitz.open(filepath)
            page = pdf.load_page(0)
            pixels = page.get_pixmap(dpi=300)
            buffer = BytesIO(pixels.tobytes(output="jpeg"))
            binary_data = buffer.getvalue()
        else:
            binary_data = None

        return (
            Book(
                id=book.id,
                filename=self.slugify(isbn, title, filepath_suffix[1:]),
                isbn=isbn,
                call_number=call_number,
                title=title,
                authors=authors,
                publisher=publisher,
                series=series,
                edition=edition,
                volume=volume,
                year=year,
                url=url,
                description=description,
                notes=notes,
            ),
            binary_data,
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

            metadata = {
                "title": result.title,
                "authors": str(result.authors)[1:-1].replace("'", ""),
                "call_number": result.call_number,
                "page": result.page,
            }

            for name, value in metadata.items():
                formatted_name = name.replace("_", " ").title()
                print(f"  {formatted_name:>{len('call_number')}} : {value}")
            print()

    def print_help(self):
        commands = {
            ":add [path]": "adds a book to the library given by the path.",
            ":clear": "clears the console window.",
            ":edit [id]": "edits the book's metadata.",
            ":exam": "creates an exam by randomly picking 3 books in the library and generates questions based off of it.",
            ":go [#]": "opens the file based on the search result #.",
            ":help": "displays a list of commands.",
            ":info [#]": "shows metadata about a given book identified by search result #.",
            ":legal": "shows all the legal notices.",
            ":question [prompt]": "prompts the llm with context from ordinary search.",
            ":remove [id]": "removes the book idenitifed by id from the book catalog.",
        }
        print("Commands:")
        for command, info in commands.items():
            print(f"  {command:<25} {info.capitalize()}")

    def db_refresh_check(self):
        if self.librarian.is_database_mismatch():
            print(
                "There is a mismatch between the catalog manager and the vector database records."
            )
            choice = prompt(
                "🔃 A database refresh is recommended. Proceed? (y/n) "
            ).lower()
            if choice == "y":
                self.librarian.refresh()
            elif choice != "n":
                raise ValueError("unknown choice {choice}")

    def cleanup(self):
        self.librarian.close()
