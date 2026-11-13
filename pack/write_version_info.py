"""Write a PyInstaller VSVersionInfo file from hearth.__version__."""

from __future__ import annotations

from pathlib import Path


def version_tuple(text: str) -> tuple[int, int, int, int]:
    core = (text or "0.0.0").split("-", 1)[0]
    parts = [int(p) if p.isdigit() else 0 for p in core.split(".")[:3]]
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2], 0)


def write_version_info(dest: Path, version: str, *, company: str = "Ahmi Darrow") -> Path:
    major, minor, patch, build = version_tuple(version)
    dotted = f"{major}.{minor}.{patch}.{build}"
    display = version
    dest = Path(dest)
    dest.write_text(
        "\n".join(
            [
                "VSVersionInfo(",
                "  ffi=FixedFileInfo(",
                f"    filevers=({major}, {minor}, {patch}, {build}),",
                f"    prodvers=({major}, {minor}, {patch}, {build}),",
                "    mask=0x3F,",
                "    flags=0x0,",
                "    OS=0x40004,",
                "    fileType=0x1,",
                "    subtype=0x0,",
                "    date=(0, 0)",
                "  ),",
                "  kids=[",
                "    StringFileInfo([",
                "      StringTable(",
                "        '040904B0',",
                "        [",
                f"          StringStruct('CompanyName', {company!r}),",
                "          StringStruct('FileDescription', 'Hearth - HomeGroup for the whole house'),",
                f"          StringStruct('FileVersion', {display!r}),",
                "          StringStruct('InternalName', 'Hearth'),",
                f"          StringStruct('LegalCopyright', 'Copyright (c) 2026 {company}'),",
                "          StringStruct('OriginalFilename', 'Hearth.exe'),",
                "          StringStruct('ProductName', 'Hearth'),",
                f"          StringStruct('ProductVersion', {display!r})",
                "        ]",
                "      )",
                "    ]),",
                "    VarFileInfo([VarStruct('Translation', [1033, 1200])])",
                "  ]",
                ")",
                "",
            ]
        ),
        encoding="utf-8",
    )
    # Keep dotted unused warning away by embedding it as FileVersion comments... actually FileVersion uses display.
    _ = dotted
    return dest


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from hearth import __version__

    out = Path(__file__).resolve().parent / "Hearth.fileinfo"
    write_version_info(out, __version__)
    print(out)
