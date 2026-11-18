import glob
import os
import shutil


def find_executable(name):
    found = shutil.which(name)
    if found:
        return found

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None

    matches = glob.glob(
        os.path.join(local_app_data, "Microsoft", "WinGet", "Packages", "*", "*", "bin", name + ".exe")
    )
    return matches[0] if matches else None


def executable_path(name):
    found = find_executable(name)
    if not found:
        raise RuntimeError(
            f"{name} not found. Install FFmpeg and add it to PATH "
            f"(on Windows: winget install Gyan.FFmpeg)."
        )
    return found
