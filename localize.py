"""
Translate the README.md into other languages (i.e. Mandarin).
"""

from openai import Client
from pathlib import Path

# TODO: Need to localize the entire package for all the "Natural Language" listed languages under pypi.org filter
languages = {"cn": "simplified chinese"}

# Use a very strong model for the best results
client = Client(base_url="", api_key="")
for abbreviation, language in languages:
    response = client.responses.create(instructions="", input="")
    Path(f"README-{abbreviation.upper()}.md").write_text(response.output_text)
