"""XMLB compilation wrapper — uses xmlb-compile.exe for Raven text→binary,
and Python xmlb.py for binary→ET.Element (decompilation/import)."""

import os
import subprocess
from pathlib import Path


def _get_compiler_exe():
    """Return the path to xmlb-compile.exe bundled with the addon."""
    here = Path(__file__).parent
    exe = here / "bin" / "xmlb-compile.exe"
    if not exe.exists():
        raise FileNotFoundError(
            f"xmlb-compile.exe not found at {exe}. "
            "Ensure it is placed in igb_blender/mapmaker/bin/."
        )
    return str(exe)


def compile_xml_to_xmlb(xml_path, output_path):
    """Compile a Raven text-format file to XMLB binary using xmlb-compile.exe.

    Args:
        xml_path: Path to input text file (Raven XMLB text format)
        output_path: Path to output .engb/.chrb/.navb/.boyb/.pkgb file
    """
    xml_path = Path(xml_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    exe = _get_compiler_exe()

    # The exe hangs after writing output (doesn't exit cleanly),
    # so we use a timeout and check if the output file was created.
    try:
        subprocess.run(
            [exe, str(xml_path), str(output_path)],
            timeout=10,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        pass  # Expected — exe doesn't exit cleanly

    if not output_path.exists():
        raise RuntimeError(
            f"XMLB compilation failed: {output_path} was not created. "
            f"Input: {xml_path}"
        )


def decompile_xmlb(xmlb_path):
    """Decompile an XMLB binary file to an ET.Element tree.

    Uses the Python xmlb.py reader (which produces standard ET trees).

    Args:
        xmlb_path: Path to input .engb/.chrb/.navb/.boyb/.pkgb file

    Returns:
        ET.Element root of the decompiled XML tree
    """
    from .xmlb import read_xmlb
    return read_xmlb(Path(xmlb_path))


def compile_all_xmlb(output_dir):
    """Compile all generated text files in output_dir to XMLB binary.

    Looks for *.engb.xml, *.chrb.xml, etc. and compiles each.

    Returns:
        Number of files compiled.
    """
    output_dir = Path(output_dir)
    compiled = 0

    # Map of text extension -> binary extension
    ext_map = {
        '.engb.xml': '.engb',
        '.chrb.xml': '.chrb',
        '.navb.xml': '.navb',
        '.boyb.xml': '.boyb',
        '.pkgb.xml': '.pkgb',
    }

    for xml_file in sorted(output_dir.iterdir()):
        name = xml_file.name
        for xml_ext, bin_ext in ext_map.items():
            if name.endswith(xml_ext):
                base = name[:-len(xml_ext)]
                bin_path = output_dir / (base + bin_ext)
                compile_xml_to_xmlb(xml_file, bin_path)
                compiled += 1
                break

    return compiled
