#!/usr/bin/env -S uv run -q
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "toml",
# ]
# ///
import logging
import os
import re
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import toml

ROOT_DIR = Path(__file__).parent
CBFLIB_DIR = ROOT_DIR / "cbflib"

re_toml_hashlines = re.compile("^version ?=|Cython", re.I)


def hash_files(*files, extra_data: Iterable[str] = None) -> str:
    """
    Generate a combined checksum for a list of files.

    For validating the the generated output file is the latest generated
    from the input sources. Equivalent to running the command:

        sha256sum <files> | sort | sha256sum

    If extra_data is provided - this is treated as though there was a
    file called "extra_contents" containing the iterable items,
    concatenated with newlines, and with a trailing newline.
    """
    hashes = []
    for filename in sorted(files):
        h = sha256()
        h.update(filename.read_bytes())
        hashes.append(h.hexdigest() + "  " + filename.name)
    if extra_data:
        h = sha256()
        h.update("\n".join(extra_data).encode() + b"\n")
        hashes.append(h.hexdigest() + "  " + "extra_data")
    hashes = sorted(hashes)
    print("\n".join(hashes))
    hashes.append("")
    # Make a combined checksum for this
    h = sha256()
    h.update("\n".join(hashes).encode())
    return h.hexdigest()


def generate_combined_checksum(root):
    # Calculate the combined hash so we know if the source have changed
    swigdir = root / "swig"
    gen_files = [
        swigdir / "make_pycbf.py",
        *swigdir.glob("*.i"),
    ]
    re_toml_hashlines = re.compile("^version ?=|Cython", re.I)
    extra_data = [
        x
        for x in (root / "pyproject.toml").read_text().splitlines()
        if re_toml_hashlines.search(x)
    ]
    swig_combined_hash = hash_files(*gen_files, extra_data=extra_data)
    hash_header = (
        "# Generated from:\n"
        + "\n".join("# - " + x.name for x in sorted(gen_files, key=lambda x: x.name))
        + "\n"
        + "\n".join("# + " + x for x in extra_data)
        + "\n#\n# Combined Checksum: "
        + swig_combined_hash
    )
    return swig_combined_hash, hash_header


def check_call(command, *args, **kwargs):
    command_str = command if isinstance(command, str) else " ".join(command)
    print(f"\n\nRunning {command_str}")
    print("=" * (len(command_str) + len("Running ")))

    return subprocess.check_call(command, *args, **kwargs)


def check_output(command, *args, **kwargs):
    print("Running ", command if isinstance(command, str) else " ".join(command))
    return subprocess.check_output(command, *args, **kwargs)


def check_tool(
    name: str,
    version_args: List[str] = [],
    version_re: Optional[str] = None,
    expect_fail: bool = False,
    minimum_version: Tuple[int] = None,
) -> bool:
    """
    Checks if an external tool is present

    Args:
        name: The name of the tool executable
        version_args: Any extra arguments to request version number printing
        version_re: The regex string to extract the version number
        expect_fail:
            Will the command return a failing exit code when called? If
            this is set to True, then this will still count as presence.
    """
    print(f"{'Checking ' + name + ': ':20}", end="")
    try:
        output = subprocess.check_output(
            [name] + version_args, stderr=subprocess.STDOUT, encoding="utf-8"
        )
    except (FileNotFoundError, PermissionError):
        print("FAIL")
        return False
    except subprocess.CalledProcessError:
        if not expect_fail:
            print("FAIL")
            return False
    if version_re:
        version = re.search(version_re, output)
        if not version:
            print("FAIL")
            logger.warning("Warning: Could not extract version for %s", name)
            return False
        version_str = version.group(1)
        ver = tuple(int(x) for x in re.split(r"[-.]", version_str))
        if ver < minimum_version:
            print("FAIL")
            logger.error(
                "%s version too low:  %s < %s",
                name,
                ".".join(str(x) for x in ver),
                ".".join(str(x) for x in minimum_version),
            )
            return False
        print("OK  " + ".".join(str(x) for x in ver))
    else:
        print("OK  ")
    return True


if __name__ == "__main__":
    logger = logging.getLogger()
    logging.basicConfig(format="%(message)s")

    # Verify that we have the right versions of everything to regenerate
    tools_available = {
        "git": check_tool("git", ["--version"]),
        "patch": check_tool("patch", ["-v"]),
        "swig": check_tool(
            "swig", ["-version"], r"SWIG Version (\d[^\s]+)", minimum_version=(4, 0, 0)
        ),
    }

    try:
        browser_dump_tool = next(
            n for n in ["links", "lynx", "elinks"] if check_tool(n, ["-version"])
        )
    except StopIteration:
        sys.exit("Error: Could not find browser dumping tool [lynx, links]")

    could_not_find = {x for x in tools_available if not tools_available[x]}
    if could_not_find:
        sys.exit("Error: Some regeneration tooling is missing. Cannot regenerate.")

    # Reset the sub-repository
    try:
        check_call(["git", "diff", "--no-ext-diff", "--quiet"], cwd=CBFLIB_DIR)
    except subprocess.CalledProcessError:
        # We have changes - reset it
        print("cbflib subdirectory has changes - resetting to reapply")
        check_call(["git", "reset", "--hard", "HEAD"], cwd=CBFLIB_DIR)
    else:
        print("\nNo changes - OK to continue\n")

    # print("Applying patches from patches/")
    # patch_dir = ROOT_DIR / "patches"
    # for patch in sorted(x for x in patch_dir.iterdir() if re.match(r"\d+[-_]", x.name)):
    #    print("Applying patch", patch)
    #    with open(patch, "rb") as f:
    #        subprocess.check_call(["patch", "-p1"], stdin=f, cwd=CBFLIB_DIR)

    print("Applying version specifier patch")

    print("Starting regeneration")
    regen_dir = Path(__file__).parent / "swig"
    html_file = CBFLIB_DIR / "doc" / "CBFlib.html"
    pycbf_dir = CBFLIB_DIR / "pycbf"

    dumped_html = check_output([browser_dump_tool, "-dump", str(html_file)])
    (pycbf_dir / "CBFlib.txt").write_bytes(dumped_html)

    check_output([sys.executable, str(regen_dir / "make_pycbf.py")])

    combined_hash, hash_header = generate_combined_checksum(ROOT_DIR)
    print("Combined hash header:\n" + hash_header)

    # Finally, regenerate with swig
    check_call(["swig", "-v", "-python", "pycbf.i"], cwd=regen_dir)

    # Take the pycbf.py, move it to this folder and add the header line
    print("\n\nCopying over and amending pycbf.py")
    pycbf_data = (regen_dir / "pycbf.py").read_bytes()

    # # Rewrite the header to add # coding
    pycbf_data = (
        b"# AUTOMATICALLY GENERATED BY PYCBF WRAPPER - DO NOT EDIT\n#\n"
        + hash_header.encode()
        + b"\n\n"
        + pycbf_data
    )

    # Get the version out of our pyproject.toml
    ppt = toml.loads((ROOT_DIR / "pyproject.toml").read_text())
    new_version = '__version__ = "{}"'.format(ppt["project"]["version"]).encode()

    OLD_VER = b'__version__ = "CBFlib 0.9"'
    assert OLD_VER in pycbf_data, "Source pycbf.py doesn't appear to be version 0.9"
    pycbf_data = pycbf_data.replace(OLD_VER, new_version)

    (ROOT_DIR / "src" / "pycbf" / "_wrapper.py").write_bytes(pycbf_data)

    # Copy over the .c file to build
    print("Copying over and amending pycbf_wrap.c...")
    pycbf_wrap_data = (regen_dir / "pycbf_wrap.c").read_bytes()
    (ROOT_DIR / "pycbf_wrap.c").write_bytes(
        hash_header.replace("#", "//").encode() + b"\n\n" + pycbf_wrap_data
    )
    os.remove(regen_dir / "pycbf.py")
    os.remove(regen_dir / "pycbf_wrap.c")

    print("done.")
