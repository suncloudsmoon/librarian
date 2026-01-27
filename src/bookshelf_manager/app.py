import sys
from pathlib import Path

from .utils import get_resource_path


def get_app_path(self):
    return Path(get_resource_path("")).parent


def get_env_paths(contents: str):
    return [path.lower() for path in contents.split(";")]


def install(user=False):
    if sys.platform == "win32":
        from py_setenv import setenv

        contents = setenv("Path", user=user)
        lower_paths = get_env_paths(contents)

        original_path = str(get_app_path())
        lower_path = original_path.lower()
        if lower_path not in lower_paths:
            setenv("Path", original_path, append=True, user=user)
    else:
        raise NotImplementedError(
            "installation for non-Windows OS is not supported yet"
        )


def uninstall():
    if sys.platform == "win32":
        from py_setenv import setenv
        
        privileges = [False, True]
        for user in privileges:
            contents = setenv("Path", user=user)
            original_paths = contents.split(";")
            lower_paths = get_env_paths(contents)

            lower_path = str(get_app_path()).lower()
            if lower_path in lower_paths:
                original_paths.pop(lower_paths.index(lower_path))
                setenv("Path", ";".join(original_paths), user=user)
    else:
        raise NotImplementedError(
            "uninstallation for non-Windows OS is not supported yet"
        )


def process_args():
    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == "--install":
            mode = sys.argv[2]
            user = False if mode == "system" else True
            install(user)
        elif command == "--uninstall":
            uninstall()
        elif command == "-B":
            return False
        else:
            raise ValueError(f"unknown command {command}")
        return True
    else:
        return False

def main():
    if process_args():
        sys.exit()
    else:
        from .cmd_app import CmdApp

        app = CmdApp()
        app.start()

if __name__ == "__main__":
    main()
