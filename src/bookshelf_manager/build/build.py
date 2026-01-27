import os
import sys
import subprocess
from shutil import copy, rmtree

sys.path.append(".")
from conversions import *
from textwrap import dedent

from utils import defined_classifications


def clean():
    directories = [
        "__pycache__",
        "executables",
        "installer",
        "legal",
        "librarian",
        "models",
        "deps",
    ]
    if sys.platform == "darwin":
        directories += ["pkgroot", "UninstallScripts"]
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
    # AI model stuff
    # For now
    if sys.platform == "win32":
        dump_onnx_model("Qwen/Qwen3-0.6B", "build")

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

    windows_stuff = (
        [
            f"--hidden-import={package}"
            for package in ["py_setenv", "win32api", "win32con"]
        ]
        if sys.platform == "win32"
        else []
    )
    args = [
        "pyinstaller",
        "--noconfirm",
        "--name",
        "librarian",
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
        "app.py",
    ] + windows_stuff
    subprocess.run(args)
    print("🛠️  PyInstaller done")

    if sys.platform == "win32":
        dump_foundry_local("build")
        issc_path = os.path.join(
            os.environ["PROGRAMFILES(x86)"], "Inno Setup 6/ISCC.exe"
        )
        subprocess.run([issc_path, "build/create_installer.iss"])
    elif sys.platform == "darwin":
        raise NotImplementedError("building a PKG for MacOS is currently unsupported")

        version = "0.7.0"
        remove_pkgroot = ["rm", "-rf", "build/pkgroot"]
        make_pkgroot = ["mkdir", "-p", "build/pkgroot/opt/librarian"]
        make_bin = ["mkdir", "-p", "build/pkgroot/usr/local/bin"]
        rsync = ["rsync", "-a", "build/executables/librarian", "build/pkgroot/opt/"]

        commands = [remove_pkgroot, make_pkgroot, make_bin, rsync]
        for command in commands:
            subprocess.run(command)
        bin_params = dedent(
            """\
        #!/bin/bash
        export PROMPT_TOOLKIT_NO_CPR=1
        exec "/opt/librarian/librarian"
        """
        )
        Path("build/pkgroot/usr/local/bin/librarian").write_text(
            bin_params, encoding="utf-8"
        )
        subprocess.run(["chmod", "755", "build/pkgroot/usr/local/bin/librarian"])

        # Let's build the installer package
        Path("build/installer").mkdir(exist_ok=True)
        pkgbuild = [
            "pkgbuild",
            "--root",
            "build/pkgroot",
            "--identifier",
            "com.suncloudsmoon.librarian",
            "--version",
            version,
            "--install-location",
            "/",
            "build/installer/librarian-component.pkg",
        ]
        productbuild = [
            "productbuild",
            "--package",
            "build/installer/librarian-component.pkg",
            "build/installer/librarian-unsigned.pkg",
        ]
        for command in (pkgbuild, productbuild):
            subprocess.run(command)

        # Creates the uninstaller package
        rm_dir = ["rm", "-rf", "build/UninstallScripts"]
        make_dirs = ["mkdir", "-p", "build/UninstallScripts"]

        for command in (rm_dir, make_dirs):
            subprocess.run(command)

        Path("build/UninstallScripts/postinstall").write_text(
            dedent(
                """
        #!/bin/sh
        
        LOG="/var/log/librarian-uninstall.log"
        exec >>"$LOG" 2>&1
        set -x   # trace, but DO NOT use -e
        
        PKG_ID="com.suncloudsmoon.librarian"
        
        echo "=== Librarian uninstall start $(date) ==="
        
        # If not installed, exit cleanly
        if ! pkgutil --pkg-info "$PKG_ID" >/dev/null 2>&1; then
          echo "Package not installed: $PKG_ID"
          exit 0
        fi
        
        # Remove installed files (best-effort)
        pkgutil --files "$PKG_ID" | while IFS= read -r rel; do
          path="/$rel"
          rm -f "$path" 2>/dev/null || true
        done
        
        # Remove known install root
        rm -rf /opt/librarian || true
        
        # Forget receipt
        pkgutil --forget "$PKG_ID" || true
        
        echo "=== Librarian uninstall end $(date) ==="
        exit 0
        """
            ),
            encoding="utf-8",
        )
        subprocess.run(["chmod", "755", "build/UninstallScripts/postinstall"])

        pkgbuild = [
            "pkgbuild",
            "--nopayload",
            "--scripts",
            "build/UninstallScripts",
            "--identifier",
            "com.suncloudsmoon.librarian.uninstall",
            "--version",
            version,
            "build/installer/Uninstall-Librarian.pkg",
        ]
        subprocess.run(pkgbuild)
    else:
        raise NotImplementedError("this stage of build not supported on your OS")

    print("🛠️  Build complete")


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) > 0 and args[0] == "clean":
        clean()
    else:
        main()
