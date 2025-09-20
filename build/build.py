
import os
import sys
import subprocess
from shutil import copy, rmtree

sys.path.append(".")
from conversions import create_classification_system, create_legal

from utils import defined_classifications


def clean():
    directories = [
        "__pycache__",
        "hooks/__pycache__",
        "executables",
        "installer",
        "legal",
        "librarian",
    ]
    files = ["file_version_info.txt", "librarian.spec"]

    for dir in directories:
        try:
            path = f"build/{dir}"
            rmtree(path)
        except:
            print(f"❌ Could not remove '{path}'")
    for file in files:
        try:
            path = f"build/{file}"
            os.remove(path)
        except:
            print(f"❌ Could not remove '{path}'")

    print("🧹 Cleaning is done")


def main():
    # create dewey.json
    for system in defined_classifications:
        create_classification_system(
            dest_dir="data",
            data_dir=f"build/classification_systems/{system}",
            type=system,
        )
    create_legal(
        legal_path="build/legal/CREDITS.txt", notice_path="build/legal/NOTICE.txt"
    )
    copy("LICENSE.txt", "build/legal/LICENSE.txt")

    # create metadata for the exe file (on Windows)
    args = [
        "pyivf-make_version",
        "--source-format",
        "yaml",
        "--metadata-source",
        "build/metadata.yml",
        "--outfile",
        "build/file_version_info.txt",
    ]
    subprocess.run(args)
    print("🛠️  EXE metadata created")

    windows_stuff = "--hidden-import=py_setenv" if sys.platform == "win32" else ""
    args = [
        "pyinstaller",
        "--name",
        "librarian",
        "--additional-hooks-dir=build/hooks",
        "--add-data",
        f"../models{os.pathsep}models",
        "--add-data",
        f"../data{os.pathsep}data",
        "--icon",
        "icon.png",
        "--version-file",
        "file_version_info.txt",
        "--specpath",
        "build",
        "--distpath",
        "build/executables",
        windows_stuff,
        "app.py",
    ]
    subprocess.run(args)
    print("🛠️  PyInstaller done")

    if sys.platform == "win32":
        issc_path = os.path.join(
            os.environ["PROGRAMFILES(x86)"], "Inno Setup 6/ISCC.exe"
        )
        subprocess.run([issc_path, "build/create_installer.iss"])

    print("🛠️  Build complete")


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) > 0 and args[0] == "clean":
        clean()
    else:
        main()
