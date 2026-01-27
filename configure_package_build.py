import shutil
import subprocess
from pathlib import Path

import toml

requirements = Path("requirements.txt").read_text(encoding="utf-8")


core_dependencies = [
    "openai",
    "Send2Trash",
    "dacite",
    "Markdown",
    "prompt_toolkit",
    "PyMuPDF",
    "platformdirs",
    "lancedb",
    "semchunk",
    "cryptography",
    "pikepdf",
    "PyYAML",
    "pypandoc",
    "langchain-huggingface",
    "langchain-community",
    "foundry-local-sdk",
    "sentence-transformers"
]
os_specific = {
    "windows": ["pywin32"]
}
dependencies = []

for line in requirements.splitlines():
    name, version = line.split("==")

    if name in core_dependencies:
        dependencies.append(f"{name} >= {version}")

# Add OS-specific dependencies
for os, package_list in os_specific.items():
    for package in package_list:
        dependencies.append(f"{package}; platform_system == '{os.capitalize()}'")

package = toml.load("pyproject.toml")
package["project"]["dependencies"] = dependencies

toml_stuff = toml.dumps(package)
Path("pyproject.toml").write_text(toml_stuff, encoding="utf-8")

# Create a dir full of wheels
wheelhouse_dir = Path("dist/wheelhouse")
if wheelhouse_dir.exists():
    shutil.rmtree(wheelhouse_dir)
wheelhouse_dir.mkdir(parents=True, exist_ok=True)
subprocess.run(["pip", "wheel", "--wheel-dir", "dist/wheelhouse", "-r", "requirements.txt"])
