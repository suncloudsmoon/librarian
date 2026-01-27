"""
Imagine that a librarian has a teacher friend that teaches to the local population
with respect to local culture/heritage, interests, among other things.

The teacher will look over all the books stored in the library and create
a multiple choice, true/false, free response questions. These questions are
meant to be created daily.

A smart model is necessary for the digestion of material. The model
needs the vision feature in order to understand where the problems are
located in say, a textbook and copy+paste them. A smarter model will grade
the questions (i.e. Gemini/ChatGPT).

For test creation, random or questions where the person failed books are sourced daily.
Deeper abstract questions are also sourced from the books and the response format
for those need to be free-response.

Brainstorm
- IV League Mentality mode?
- Sections on different subject matters like philosophy?
- Lesson on geography, like where countries are located

"""

import base64
import os
import random
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from typing import Literal

import fitz
import pymupdf
import pypandoc
import yaml
from openai import OpenAI
from pikepdf import Pdf
from pydantic import BaseModel, Field

from .catalog_manager import Book
from .librarian import Librarian


class Question(BaseModel):
    content: str = Field(title="Content", description="Body of the question")
    choice_a: str = Field(
        title="Choice A",
        description="First choice given in the multiple-choice question",
    )
    choice_b: str = Field(
        title="Choice B",
        description="Second choice given in the multiple-choice question",
    )
    choice_c: str = Field(
        title="Choice C",
        description="Third choice given in the multiple-choice question. Can be left blank if appropriate.",
    )
    choice_d: str = Field(
        title="Choice D",
        description="Fourth choice given in the multiple-choice question. Can be left blank if appropriate.",
    )
    correct_choice: Literal["A", "B", "C", "D"] = Field(
        title="Correct Choice", description="Correct answer letter"
    )


@dataclass
class QuestionInfo:
    question: Question
    book: Book
    page: int


@dataclass
class ExamSection:
    book: Book
    questions: list[QuestionInfo]


class Teacher:

    def __init__(self, librarian: Librarian, chat_client: OpenAI, model_name: str):
        self.librarian = librarian
        self.chat_client = chat_client
        self.model_name = model_name
        self.system_prompt = (
            "Your job is to extract questions and answers from a page selected from a textbook for preparing an exam for a college student. "
            "The question(s) should be extracted from the text only. For each question, there should be 4 choices populated. "
            "If the question refers to any content in the page, some context must be embedded within the question to make it easier for the reader to find the content. "
            "The correct choice should not have any special formatting that indicates that it is the correct answer. "
            "The choices should be populated by referring to the surrounding details in the page if possible. "
            "If the page does not show any useful content, like the table of contents or index, then return empty strings for the question content and choices. "
            "If a true/false choice is more suitable, populate choices 1 and 2 with 'True' and 'False' and leave choices 3 and 4 blank. "
            "The output content for the question and the choices should only contain the text and should not contain numbering or other prefixes. "
            "The output content should conform to markdown formatting guidelines. "
            "LaTeX should be used as minimally as possible. "
            "LaTeX should be used within markdown only for representing math symbols and should be wrapped with math delimiters (e.g. $a^b$). "
            "Because you are outputting JSON, make sure to escape all backslahes (e.g. \\\\frac{})."
        )

    def export_exam(self, filetype: str, filepath: Path, exam: list[ExamSection]):
        if filetype == "pdf":
            book_titles = [section.book.title for section in exam]

            yaml_header = yaml.dump(
                {
                    "title": "Exam",
                    "author": "librarian",
                    "subject": "Exam document",
                    "keywords": [title for title in book_titles],
                }
            )

            contents = "---\n" + yaml_header + "---\n"

            # Some formatting
            contents += dedent(
                r"""
                \pagenumbering{roman}
                \tableofcontents
                
                \newpage
                \pagenumbering{arabic}
                """
            )

            for section in exam:
                contents += f"# {section.book.title}\n"
                for i, question_info in enumerate(section.questions, start=1):
                    question = question_info.question
                    contents += f"## Question {i} (p. {question_info.page})\n"
                    contents += f"{self.clean_latex(question.content)}\n\n"
                    for letter, choice in {
                        "a": question.choice_a,
                        "b": question.choice_b,
                        "c": question.choice_c,
                        "d": question.choice_d,
                    }.items():
                        if choice:
                            contents += (
                                f"{letter.upper()}.  {self.clean_latex(choice)}\n"
                            )
                    contents += "\n\n"

            # Add blank appendix page
            contents += self.get_new_page() + self.get_title_page("Appendix")

            fd, path = tempfile.mkstemp(suffix=".md")
            md_file = Path(path)
            md_file.write_text(contents, encoding="utf-8")

            output_file = filepath.with_suffix(".pdf")
            pypandoc.convert_file(
                md_file,
                "pdf",
                outputfile=output_file,
                extra_args=[
                    "--toc-depth=3",
                    "-V",
                    "hyperrefoptions=linktoc=all",
                    "--pdf-engine=lualatex",
                ],
            )
            os.close(fd)

            # Create appendix and answer key
            exam_file = fitz.open(output_file)
            page_count = len(exam_file)

            toc = exam_file.get_toc()
            toc.append([1, "Appendix", page_count])
            page_count = page_count + 1

            # Appendix
            for section in exam:
                book = section.book

                with Teacher.TempPdf(self.get_title_page(book.title)) as pdf_path:
                    with fitz.open(pdf_path) as file:
                        exam_file.insert_pdf(file)
                toc.append([2, book.title, page_count])
                page_count = page_count + 1

                for question_info in section.questions:
                    filepath = self.librarian.get_book_path(question_info.book)
                    page_index = question_info.page - 1
                    with fitz.open(filepath) as file:
                        exam_file.insert_pdf(
                            docsrc=file,
                            from_page=page_index,
                            to_page=page_index,
                            annots=False,
                            widgets=False,
                            final=True,
                        )
                    toc.append([3, f"Page {page_index + 1}", page_count])
                    page_count = page_count + 1

            # Answer key
            exam_file.new_page()
            exam_file.new_page()
            toc.append([1, "Answer Key", page_count + 2])
            answer_key = "# Answer Key\n"

            for section in exam:
                answer_key += f"## {section.book.title}\n"
                for num, question_info in enumerate(section.questions, start=1):
                    question = question_info.question
                    answer_key += f"{num}. {question.correct_choice}\n"
                answer_key += "\n\n"

            # Create temporary file and combine the PDFs
            md_fd, md_filepath = tempfile.mkstemp(suffix=".md")
            Path(md_filepath).write_text(answer_key, encoding="utf-8")

            pdf_fd, pdf_filepath = tempfile.mkstemp(suffix=".pdf")
            pypandoc.convert_file(
                md_filepath,
                to="pdf",
                outputfile=pdf_filepath,
                extra_args=["--pdf-engine=xelatex"],
            )

            with fitz.open(pdf_filepath) as obj:
                exam_file.insert_pdf(obj)

            # Update the Table of Contents (ToC)
            exam_file.set_toc(toc)

            toc_fd, filepath = tempfile.mkstemp(suffix=".pdf")
            exam_file.save(
                filepath, incremental=False, garbage=4, clean=True, deflate=True
            )
            exam_file.close()

            # Create a linearized PDF for fast web viewing
            with Pdf.open(filepath) as pdf:
                pdf.save(output_file, linearize=True)

            for fd in [md_fd, pdf_fd, toc_fd]:
                os.close(fd)

            # os.replace(filepath, output_file)
        elif filetype == "text":
            text = "Exam\n"
            for section in exam:
                text += f"\n{section.book.title}\n\n"
                for i, question in enumerate(section.questions):
                    text += f"{i+1}. {self.get_readable_question(question)}\n"
            filepath.with_suffix(".txt").write_text(text, encoding="utf-8")
        else:
            raise NotImplementedError(f"unknown filetype {filetype}")

    class TempPdf:
        def __init__(self, markdown_stuff: str):
            self.markdown_stuff = markdown_stuff

        def __enter__(self):
            # Create temporary file and combine the PDFs
            md_fd, md_filepath = tempfile.mkstemp(suffix=".md")
            Path(md_filepath).write_text(self.markdown_stuff, encoding="utf-8")

            self.pdf_fd, pdf_filepath = tempfile.mkstemp(suffix=".pdf")
            pypandoc.convert_file(
                md_filepath,
                to="pdf",
                outputfile=pdf_filepath,
                extra_args=["--pdf-engine=xelatex"],
            )
            os.close(md_fd)
            return Path(pdf_filepath)

        def __exit__(self, exc_type, exc, tb):
            os.close(self.pdf_fd)

    def get_title_page(self, title: str) -> str:
        return dedent(
            rf"""
            \thispagestyle{{empty}}
            
            \vspace*{{\fill}}
            \begin{{center}}
            {{\Huge \textbf{{{title}}}}}
            \end{{center}}
            \vspace*{{\fill}}
            """
        )

    def get_new_page(self) -> str:
        return r"\newpage" + "\n"

    def clean_latex(self, contents: str) -> str:
        return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", contents)

    def get_readable_question(self, question: Question):
        return dedent(
            f"""\
        {question.content}
        A. {question.choice_a}
        B. {question.choice_b}
        C. {question.choice_c}
        D. {question.choice_d}
        """
        ).strip()

    def create_exam(
        self, num_book_selections: int = 3, limit_per_section: int | None = 5
    ) -> list[ExamSection]:
        books = [
            book
            for book in self.librarian.catalog_manager.books
            if book.filename.lower().endswith(".pdf")
        ]
        if len(books) < num_book_selections:
            raise RuntimeError(
                f"The library needs to contain at least {num_book_selections} books in order to create an exam"
            )
        exam = []  # key = book, value = question list
        for i in range(num_book_selections):
            book = random.choice(books)
            books.remove(book)
            questions: QuestionInfo = self.generate_questions(book)
            if limit_per_section:
                questions = random.sample(
                    questions, min(len(questions), limit_per_section)
                )
            if questions:
                exam.append(ExamSection(book, questions))
        return exam

    def generate_questions(self, book: Book) -> list[QuestionInfo]:
        filepath = self.librarian.get_book_path(book)
        questions = []
        doc = pymupdf.open(filepath)
        page_indicies = [random.randint(0, len(doc) - 1) for i in range(5)]
        for index in page_indicies:
            pix = doc[index].get_pixmap()
            bytes = pix.tobytes(output="jpeg")
            image_data = base64.urlsafe_b64encode(bytes).decode()
            question = self.ask(image_data)
            if question.content and (
                question.choice_a
                or question.choice_b
                or question.choice_c
                or question.choice_d
            ):
                questions.append(QuestionInfo(question, book, index + 1))
        return questions

    def ask(self, image_data: str) -> Question:
        completion = self.chat_client.chat.completions.parse(
            model=self.model_name,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_data}"
                            },
                        }
                    ],
                },
            ],
            response_format=Question,
            max_completion_tokens=1000,
        )
        message = completion.choices[0].message
        return message.parsed
