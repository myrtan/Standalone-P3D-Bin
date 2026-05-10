from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


APP_NAME = "DayZ P3D Binarizer"
APPDATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DayZ_P3D_Binarizer"
PORTABLE_CONFIG = "p3d_binarizer_config.json"

REFERENCE_EXTENSIONS = (
    "paa",
    "rvmat",
    "emat",
    "edds",
    "p3d",
    "wss",
    "ogg",
    "cfg",
    "cpp",
    "hpp",
    "h",
    "bisurf",
    "ptc",
)
TEXT_REFERENCE_EXTENSIONS = {".rvmat", ".emat", ".cfg", ".cpp", ".hpp", ".h"}
TEXT_REFERENCE_RE = re.compile(
    r"[\"']([^\"'\r\n]+\.(?:%s))[\"']" % "|".join(REFERENCE_EXTENSIONS),
    re.IGNORECASE,
)
REFERENCE_EXTENSION_BYTES = tuple(("." + ext).encode("ascii") for ext in REFERENCE_EXTENSIONS)
REFERENCE_PATH_BYTES = set(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    b"abcdefghijklmnopqrstuvwxyz"
    b"0123456789"
    b"_@#$%&()-+{}[],.;: /\\"
)
ENGINE_REFERENCE_ROOTS = {"a3", "bin", "ca", "core", "dta", "dz", "languagecore"}

DEFAULT_SETTINGS = {
    "project_root": "P:",
    "binarize_exe": "",
    "max_processes": 0,
    "output_folder_name": "_binarized",
    "isolated_project_root": True,
    "pause_on_exit": True,
    "continue_on_missing_references": False,
}


class AppError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReferenceInfo:
    source: str
    reference: str
    resolved_path: Path
    exists: bool


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def local_settings_path() -> Path:
    return APPDATA_DIR / "settings.json"


def load_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    for path in (local_settings_path(), app_dir() / PORTABLE_CONFIG):
        try:
            if path.is_file():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    settings.update(loaded)
        except Exception:
            pass

    if not settings.get("binarize_exe"):
        found = find_binarize_exe()
        if found:
            settings["binarize_exe"] = str(found)

    return settings


def save_settings(settings: dict) -> None:
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    path = local_settings_path()
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")


def find_binarize_exe() -> Path | None:
    candidates = []
    env_path = os.environ.get("DAYZ_BINARIZE_EXE")
    if env_path:
        candidates.append(Path(env_path))

    base = app_dir()
    candidates.extend(
        [
            base / "binarize.exe",
            base / "Binarize" / "binarize.exe",
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
            / "Steam/steamapps/common/DayZ Tools/Bin/Binarize/binarize.exe",
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Steam/steamapps/common/DayZ Tools/Bin/Binarize/binarize.exe",
            Path("C:/Program Files (x86)/Steam/steamapps/common/DayZ Tools/Bin/Binarize/binarize.exe"),
            Path("C:/Program Files/Steam/steamapps/common/DayZ Tools/Bin/Binarize/binarize.exe"),
        ]
    )

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def normalize_project_root_arg(project_root: Path) -> str:
    value = str(project_root)
    return value.rstrip("\\/")


def normalize_working_dir(project_root: Path) -> str:
    value = normalize_project_root_arg(project_root)
    if len(value) == 2 and value[1] == ":":
        return value + "\\"
    return value


def read_magic(path: Path) -> bytes:
    try:
        with path.open("rb") as file:
            return file.read(4)
    except OSError:
        return b""


def is_odol(path: Path) -> bool:
    return read_magic(path) == b"ODOL"


def is_mlod(path: Path) -> bool:
    return read_magic(path) == b"MLOD"


def iter_ascii_chunks(data: bytes, minimum_length: int = 5) -> Iterable[bytes]:
    chunk = bytearray()
    for value in data:
        if 32 <= value <= 126:
            chunk.append(value)
            continue
        if len(chunk) >= minimum_length:
            yield bytes(chunk)
        chunk.clear()
    if len(chunk) >= minimum_length:
        yield bytes(chunk)


def iter_binary_reference_strings(data: bytes) -> Iterable[str]:
    seen = set()

    for chunk in iter_ascii_chunks(data):
        lower = chunk.lower()
        for extension in REFERENCE_EXTENSION_BYTES:
            search_from = 0
            while True:
                index = lower.find(extension, search_from)
                if index < 0:
                    break

                end = index + len(extension)
                start = index
                while start > 0 and chunk[start - 1] in REFERENCE_PATH_BYTES:
                    start -= 1

                value = chunk[start:end].decode("ascii", errors="ignore").strip()
                key = normalize_reference(value).lower()
                if key and key not in seen:
                    seen.add(key)
                    yield value
                search_from = end


def iter_text_reference_strings(path: Path) -> Iterable[str]:
    try:
        data = path.read_bytes()
    except OSError:
        return

    seen = set()
    try:
        content = data.decode("utf-8", errors="ignore")
    except UnicodeDecodeError:
        content = ""

    for match in TEXT_REFERENCE_RE.finditer(content):
        value = match.group(1)
        key = normalize_reference(value).lower()
        if key and key not in seen:
            seen.add(key)
            yield value

    for value in iter_binary_reference_strings(data):
        key = normalize_reference(value).lower()
        if key and key not in seen:
            seen.add(key)
            yield value


def normalize_reference(reference: str) -> str:
    value = reference.strip().strip('"').strip("'")
    value = value.replace("/", "\\")
    return value.strip()


def reference_root(reference: str) -> str:
    value = normalize_reference(reference)
    drive, tail = os.path.splitdrive(value)
    if drive:
        value = tail.lstrip("\\/")
    else:
        value = value.lstrip("\\/")
    parts = [part for part in value.split("\\") if part]
    return parts[0].lower() if len(parts) >= 2 else ""


def resolve_addon_root(p3d_path: Path, project_root: Path) -> Path | None:
    try:
        rel = p3d_path.resolve(strict=False).relative_to(project_root.resolve(strict=False))
    except ValueError:
        return None
    if not rel.parts:
        return None
    return project_root / rel.parts[0]


def paths_overlap(path_a: Path, path_b: Path) -> bool:
    try:
        a = path_a.resolve(strict=False)
        b = path_b.resolve(strict=False)
    except Exception:
        return False

    try:
        a.relative_to(b)
        return True
    except ValueError:
        pass

    try:
        b.relative_to(a)
        return True
    except ValueError:
        return False


def find_project_root_child(project_root: Path, root_name: str) -> Path | None:
    project_root = Path(normalize_working_dir(project_root))
    if not root_name or not project_root.is_dir():
        return None

    direct = project_root / root_name
    if direct.is_dir():
        return direct

    root_key = root_name.lower()
    try:
        for entry in project_root.iterdir():
            if entry.is_dir() and entry.name.lower() == root_key:
                return entry
    except OSError:
        return None

    return None


def project_root_child_for_path(path: Path, project_root: Path) -> tuple[str, Path] | None:
    project_root = Path(normalize_working_dir(project_root))
    try:
        rel = path.resolve(strict=False).relative_to(project_root.resolve(strict=False))
    except ValueError:
        return None
    if not rel.parts:
        return None
    child = find_project_root_child(project_root, rel.parts[0])
    if not child:
        return None
    return child.name, child


def infer_source_addon_root(p3d_path: Path, project_root: Path) -> tuple[Path, str, Path]:
    project_root = Path(normalize_working_dir(project_root))

    try:
        rel = p3d_path.resolve(strict=False).relative_to(project_root.resolve(strict=False))
        if rel.parts:
            addon_name = rel.parts[0]
            addon_root = project_root / addon_name
            return addon_root, addon_name, Path(*rel.parts[1:])
    except ValueError:
        pass

    for ancestor in p3d_path.parents:
        if not ancestor.name:
            break
        if find_project_root_child(project_root, ancestor.name):
            return ancestor, ancestor.name, p3d_path.relative_to(ancestor)

    return p3d_path.parent, p3d_path.parent.name, Path(p3d_path.name)


def safe_folder_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.@#$%&()+={}\[\],; -]+", "_", value.strip())
    cleaned = cleaned.strip(" .")
    return cleaned or "addon"


def embedded_project_reference_variants(value: str, project_root: Path) -> list[str]:
    project_root = Path(normalize_working_dir(project_root))
    if not project_root.is_dir():
        return []

    lower_value = value.lower()
    variants: list[str] = []
    try:
        for entry in project_root.iterdir():
            if not entry.is_dir():
                continue
            marker = entry.name.lower() + "\\"
            index = lower_value.find(marker)
            if index > 0:
                variants.append(value[index:])
    except OSError:
        return []
    return variants


def copy_tree(source: Path, target: Path) -> None:
    ignore = shutil.ignore_patterns(".git", ".svn", ".vscode", ".idea", "__pycache__", "_binarized")
    shutil.copytree(source, target, ignore=ignore)


def overlay_tree(source: Path, target: Path) -> None:
    ignore_dirs = {".git", ".svn", ".vscode", ".idea", "__pycache__", "_binarized"}
    for root, dirs, files in os.walk(source):
        dirs[:] = [dirname for dirname in dirs if dirname.lower() not in ignore_dirs]
        rel_root = Path(root).relative_to(source)
        target_root = target / rel_root
        target_root.mkdir(parents=True, exist_ok=True)
        for filename in files:
            shutil.copy2(Path(root) / filename, target_root / filename)


def collect_external_reference_roots(
    report: list[ReferenceInfo],
    project_root: Path,
    current_root_names: set[str],
) -> list[tuple[str, Path]]:
    roots: dict[str, tuple[str, Path]] = {}
    current = {name.lower() for name in current_root_names if name}

    for item in report:
        root_info = project_root_child_for_path(item.resolved_path, project_root) if item.exists else None
        if root_info:
            root_name, root_path = root_info
        else:
            root_name = reference_root(item.reference)
            root_path = find_project_root_child(project_root, root_name) if root_name else None

        if not root_name or not root_path:
            continue

        root_key = root_name.lower()
        if root_key in current or root_key in ENGINE_REFERENCE_ROOTS:
            continue

        roots[root_key] = (root_path.name, root_path)

    return [roots[key] for key in sorted(roots)]


def copy_engine_reference_files(report: list[ReferenceInfo], isolated_root: Path, project_root: Path) -> int:
    project_root = Path(normalize_working_dir(project_root))
    copied = 0

    for item in report:
        if not item.exists:
            continue

        root_info = project_root_child_for_path(item.resolved_path, project_root)
        if not root_info:
            continue

        root_name, _root_path = root_info
        if root_name.lower() not in ENGINE_REFERENCE_ROOTS:
            continue

        try:
            rel = item.resolved_path.resolve(strict=False).relative_to(project_root.resolve(strict=False))
        except ValueError:
            continue

        target = isolated_root / rel
        if target.is_file():
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.resolved_path, target)
        copied += 1

    return copied


def prepare_isolated_project(
    p3d_path: Path,
    project_root: Path,
    temp_root: Path,
    reference_report: list[ReferenceInfo],
    log_file: Path | None,
) -> tuple[Path, Path, Path]:
    source_addon_root, addon_name, relative_p3d = infer_source_addon_root(p3d_path, project_root)
    safe_addon_name = safe_folder_name(addon_name)
    isolated_root = temp_root / "project"
    isolated_addon_root = isolated_root / safe_addon_name

    isolated_root.mkdir(parents=True, exist_ok=True)
    project_addon_root = find_project_root_child(project_root, addon_name)

    if project_addon_root and not paths_overlap(project_addon_root, source_addon_root):
        log_line(f"Изолированный проект: копирую базовый аддон {project_addon_root}", log_file)
        copy_tree(project_addon_root, isolated_addon_root)
        log_line(f"Изолированный проект: накладываю локальные файлы {source_addon_root}", log_file)
        overlay_tree(source_addon_root, isolated_addon_root)
    else:
        log_line(f"Изолированный проект: копирую аддон {source_addon_root}", log_file)
        copy_tree(source_addon_root, isolated_addon_root)

    copied_roots = 0
    external_roots = collect_external_reference_roots(reference_report, project_root, {addon_name, safe_addon_name})
    for root_name, root_path in external_roots:
        target = isolated_root / root_name
        if target.exists():
            continue
        log_line(f"Изолированный проект: копирую referenced root {root_path}", log_file)
        copy_tree(root_path, target)
        copied_roots += 1

    if copied_roots:
        log_line(f"Изолированный проект: copied referenced roots: {copied_roots}", log_file)
    else:
        log_line("Изолированный проект: внешних referenced roots нет.", log_file)

    copied_engine_files = copy_engine_reference_files(reference_report, isolated_root, project_root)
    if copied_engine_files:
        log_line(f"Изолированный проект: copied engine reference files: {copied_engine_files}", log_file)

    isolated_p3d = isolated_addon_root / relative_p3d
    if not isolated_p3d.is_file():
        fallback = isolated_addon_root / p3d_path.name
        if fallback.is_file():
            isolated_p3d = fallback
        else:
            raise AppError(f"Не удалось подготовить изолированную копию P3D: {isolated_p3d}")

    return isolated_root, isolated_p3d, isolated_addon_root


def copy_model_config_context(config_file: Path, target_dir: Path) -> None:
    shutil.copy2(config_file, target_dir / config_file.name)

    for extension in ("*.cfg", "*.hpp", "*.h"):
        for sibling in config_file.parent.glob(extension):
            target = target_dir / sibling.name
            if sibling.is_file() and not target.exists():
                shutil.copy2(sibling, target)


def prepare_single_p3d_source(
    isolated_p3d: Path,
    isolated_addon_root: Path,
    temp_root: Path,
    log_file: Path | None,
) -> tuple[Path, Path]:
    source_dir = temp_root / "single_source"
    source_dir.mkdir(parents=True, exist_ok=True)

    target_p3d = source_dir / isolated_p3d.name
    shutil.copy2(isolated_p3d, target_p3d)

    copied_configs = []
    for filename in ("model.cfg", "skeleton.cfg"):
        source_file = isolated_p3d.parent / filename
        if source_file and source_file.is_file():
            copy_model_config_context(source_file, source_dir)
            copied_configs.append(source_file.name)

    for dirname in ("proxy", "proxies"):
        source_folder = isolated_p3d.parent / dirname
        target_folder = source_dir / dirname
        if source_folder.is_dir() and not target_folder.exists():
            copy_tree(source_folder, target_folder)

    log_line(f"Targeted source: подготовлен только {target_p3d.name}", log_file)
    if copied_configs:
        log_line(f"Targeted source: model config учтен: {', '.join(sorted(set(copied_configs)))}", log_file)
    else:
        log_line("Targeted source: model.cfg/skeleton.cfg не найден в папке модели.", log_file)
    return target_p3d, source_dir


def reference_candidates(reference: str, source_file: Path, p3d_path: Path, project_root: Path) -> list[Path]:
    value = normalize_reference(reference)
    if not value or value.startswith("#"):
        return []

    drive, tail = os.path.splitdrive(value)
    if drive:
        return [Path(value)]

    project_root = Path(normalize_working_dir(project_root))
    candidates: list[Path] = []

    variants = [value]
    if not value.startswith("\\"):
        variants.extend(embedded_project_reference_variants(value, project_root))
        parts = [part for part in value.split("\\") if part]
        for index in range(1, max(1, len(parts) - 1)):
            variants.append("\\".join(parts[index:]))

    for variant in variants:
        if variant.startswith("\\"):
            candidates.append(project_root / variant.lstrip("\\/"))
            continue

        root = reference_root(variant)
        if root:
            candidates.append(project_root / variant)

        candidates.append(source_file.parent / variant)

        addon_root = resolve_addon_root(p3d_path, project_root)
        if addon_root:
            candidates.append(addon_root / variant)

        if not root:
            candidates.append(project_root / variant)

    unique: list[Path] = []
    seen = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def resolve_reference(reference: str, source_file: Path, p3d_path: Path, project_root: Path) -> tuple[Path, bool]:
    candidates = reference_candidates(reference, source_file, p3d_path, project_root)
    if not candidates:
        return project_root / normalize_reference(reference), False
    for candidate in candidates:
        if candidate.is_file():
            return candidate, True
    return candidates[0], False


def collect_reference_report(p3d_path: Path, project_root: Path) -> list[ReferenceInfo]:
    report: list[ReferenceInfo] = []
    queue: list[tuple[Path, str, str]] = []
    visited_text_files = set()
    seen_refs = set()

    try:
        data = p3d_path.read_bytes()
    except OSError:
        return report

    for reference in iter_binary_reference_strings(data):
        queue.append((p3d_path, reference, p3d_path.name))

    while queue:
        source_file, reference, source_label = queue.pop(0)
        normalized = normalize_reference(reference)
        if not normalized:
            continue

        key = (str(source_file).lower(), normalized.lower())
        if key in seen_refs:
            continue
        seen_refs.add(key)

        root = reference_root(normalized)
        is_engine_reference = root in ENGINE_REFERENCE_ROOTS
        resolved, exists = resolve_reference(normalized, source_file, p3d_path, project_root)

        # Report existing DZ/engine references so isolated mode can copy the exact
        # files it needs, but avoid noisy warnings when a vanilla path is not unpacked.
        if is_engine_reference and not exists:
            continue

        report.append(ReferenceInfo(source_label, normalized, resolved, exists))

        if not exists or resolved.suffix.lower() not in TEXT_REFERENCE_EXTENSIONS:
            continue

        text_key = str(resolved.resolve(strict=False)).lower()
        if text_key in visited_text_files:
            continue
        visited_text_files.add(text_key)

        for nested in iter_text_reference_strings(resolved):
            queue.append((resolved, nested, resolved.name))

    return report


def open_p3d_dialog() -> list[str]:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return []

    root = tk.Tk()
    root.withdraw()
    root.update()
    files = filedialog.askopenfilenames(
        title="Выберите P3D для бинаризации",
        filetypes=[("DayZ P3D", "*.p3d"), ("All files", "*.*")],
    )
    root.destroy()
    return list(files)


def log_line(message: str, log_file: Path | None = None) -> None:
    print(message)
    if log_file:
        with log_file.open("a", encoding="utf-8") as file:
            file.write(message + "\n")


def print_reference_summary(report: list[ReferenceInfo], log_file: Path | None) -> list[ReferenceInfo]:
    if not report:
        log_line("Ссылки на текстуры/материалы в P3D не найдены.", log_file)
        return []

    missing = [item for item in report if not item.exists]
    found_count = len(report) - len(missing)
    log_line(f"Проверка ссылок: найдено {found_count}, отсутствует {len(missing)}.", log_file)

    if missing:
        log_line("Отсутствующие ссылки:", log_file)
        for item in missing[:30]:
            log_line(f"  - {item.reference}  ->  {item.resolved_path}", log_file)
        if len(missing) > 30:
            log_line(f"  ... и еще {len(missing) - 30}", log_file)

    return missing


def should_continue_with_missing(args: argparse.Namespace, settings: dict) -> bool:
    if args.yes or settings.get("continue_on_missing_references"):
        return True

    answer = input("Есть отсутствующие текстуры/материалы. Продолжить Binarize? [y/N]: ").strip().lower()
    return answer in {"y", "yes", "д", "да"}


def build_command(
    binarize_exe: Path,
    source_dir: Path,
    temp_output_dir: Path,
    texture_temp_dir: Path,
    project_root: Path,
    max_processes: int,
) -> list[str]:
    binpath = str(binarize_exe.parent)
    return [
        str(binarize_exe),
        "-targetBonesInterval=56",
        f"-maxProcesses={max_processes}",
        "-always",
        "-silent",
        f"-addon={normalize_project_root_arg(project_root)}",
        f"-textures={texture_temp_dir}",
        f"-binpath={binpath}",
        str(source_dir),
        str(temp_output_dir),
    ]


def run_subprocess(cmd: list[str], cwd: str, log_file: Path | None) -> int:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        cmd,
        cwd=cwd if cwd and Path(cwd).is_dir() else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        creationflags=creationflags,
    )
    assert process.stdout is not None
    for line in process.stdout:
        log_line(line.rstrip(), log_file)
    return process.wait()


def locate_binarized_output(temp_output_dir: Path, p3d_path: Path) -> Path | None:
    direct = temp_output_dir / p3d_path.name
    if direct.is_file():
        return direct

    matches = [path for path in temp_output_dir.rglob(p3d_path.name) if path.is_file()]
    if not matches:
        return None
    matches.sort(key=lambda path: len(path.parts))
    return matches[0]


def process_one_p3d(
    p3d_path: Path,
    settings: dict,
    args: argparse.Namespace,
    log_file: Path | None,
) -> Path | None:
    p3d_path = p3d_path.resolve()
    if not p3d_path.is_file():
        raise AppError(f"Файл не найден: {p3d_path}")
    if p3d_path.suffix.lower() != ".p3d":
        raise AppError(f"Это не .p3d файл: {p3d_path}")

    log_line("", log_file)
    log_line(f"Модель: {p3d_path}", log_file)

    magic = read_magic(p3d_path)
    if magic == b"ODOL" and not args.force:
        log_line("Файл уже бинаризован (ODOL). Пропускаю.", log_file)
        return None
    if magic and magic != b"MLOD":
        log_line(f"Предупреждение: сигнатура файла {magic!r}, ожидается MLOD для исходной модели.", log_file)

    binarize_exe = Path(args.binarize_exe or settings.get("binarize_exe") or "")
    project_root = Path(args.project_root or settings.get("project_root") or "P:")
    if not binarize_exe.is_file():
        raise AppError(f"binarize.exe не найден: {binarize_exe}")
    if not Path(normalize_working_dir(project_root)).is_dir():
        raise AppError(f"Project root не найден: {project_root}")

    settings["binarize_exe"] = str(binarize_exe)
    settings["project_root"] = str(project_root)

    report = collect_reference_report(p3d_path, project_root)
    missing = print_reference_summary(report, log_file)
    if missing and not should_continue_with_missing(args, settings):
        log_line("Бинаризация отменена для этой модели.", log_file)
        return None

    max_processes = int(args.max_processes or settings.get("max_processes") or 0)
    if max_processes <= 0:
        max_processes = max(1, os.cpu_count() or 4)
    settings["max_processes"] = max_processes

    output_root = Path(args.output_dir) if args.output_dir else p3d_path.parent / settings.get("output_folder_name", "_binarized")
    output_root.mkdir(parents=True, exist_ok=True)
    final_output = output_root / p3d_path.name

    run_stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    temp_root = Path(tempfile.gettempdir()) / "DayZ_P3D_Binarizer" / f"{p3d_path.stem}_{run_stamp}"
    temp_output_dir = temp_root / "out"
    texture_temp_dir = temp_root / "textures"
    temp_output_dir.mkdir(parents=True, exist_ok=True)
    texture_temp_dir.mkdir(parents=True, exist_ok=True)

    binarize_project_root = project_root
    binarize_p3d_path = p3d_path
    binarize_source_dir = p3d_path.parent
    if not args.direct and settings.get("isolated_project_root", True):
        binarize_project_root, binarize_p3d_path, isolated_addon_root = prepare_isolated_project(
            p3d_path=p3d_path,
            project_root=project_root,
            temp_root=temp_root,
            reference_report=report,
            log_file=log_file,
        )
        binarize_p3d_path, binarize_source_dir = prepare_single_p3d_source(
            isolated_p3d=binarize_p3d_path,
            isolated_addon_root=isolated_addon_root,
            temp_root=temp_root,
            log_file=log_file,
        )

    cmd = build_command(
        binarize_exe=binarize_exe,
        source_dir=binarize_source_dir,
        temp_output_dir=temp_output_dir,
        texture_temp_dir=texture_temp_dir,
        project_root=binarize_project_root,
        max_processes=max_processes,
    )

    log_line("", log_file)
    log_line("Запускаю Binarize:", log_file)
    log_line(f"  Binarize:     {binarize_exe}", log_file)
    log_line(f"  Source dir:   {binarize_source_dir}", log_file)
    log_line(f"  Project root: {normalize_project_root_arg(binarize_project_root)}", log_file)
    if binarize_project_root != project_root:
        log_line(f"  Original P:   {normalize_project_root_arg(project_root)}", log_file)
    log_line(f"  Temp output:  {temp_output_dir}", log_file)
    log_line(f"  Textures tmp: {texture_temp_dir}", log_file)
    log_line("", log_file)

    try:
        return_code = run_subprocess(cmd, normalize_working_dir(binarize_project_root), log_file)
        if return_code != 0:
            raise AppError(f"Binarize завершился с кодом {return_code}.")

        produced = locate_binarized_output(temp_output_dir, binarize_p3d_path)
        if not produced:
            raise AppError(f"Binarize не создал выходной P3D для {p3d_path.name}.")

        shutil.copy2(produced, final_output)
        if not is_odol(final_output):
            raise AppError(f"Выходной файл создан, но он не ODOL: {final_output}")

        log_line(f"Готово: {final_output}", log_file)
        return final_output
    finally:
        if args.keep_temp:
            log_line(f"Temp сохранен: {temp_root}", log_file)
        else:
            shutil.rmtree(temp_root, ignore_errors=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Drag-and-drop wrapper for DayZ Tools binarize.exe.",
    )
    parser.add_argument("paths", nargs="*", help="P3D files. Можно просто перетащить файл на exe.")
    parser.add_argument("--binarize-exe", help="Путь к DayZ Tools Bin/Binarize/binarize.exe.")
    parser.add_argument("--project-root", help="Project root, обычно P:")
    parser.add_argument("--output-dir", help="Куда положить готовый P3D. По умолчанию: _binarized рядом с моделью.")
    parser.add_argument("--max-processes", type=int, default=0, help="maxProcesses для Binarize. 0 = число потоков CPU.")
    parser.add_argument("--force", action="store_true", help="Запускать Binarize даже для ODOL-файлов.")
    parser.add_argument("--yes", action="store_true", help="Продолжать даже если найдены отсутствующие ссылки.")
    parser.add_argument("--direct", action="store_true", help="Запускать напрямую с реальным P: без изолированного project root.")
    parser.add_argument("--keep-temp", action="store_true", help="Не удалять временную папку после запуска.")
    parser.add_argument("--no-pause", action="store_true", help="Не ждать Enter в конце.")
    return parser.parse_args(argv)


def pause_if_needed(args: argparse.Namespace, settings: dict) -> None:
    if args.no_pause or not settings.get("pause_on_exit", True):
        return
    try:
        input("\nНажмите Enter, чтобы закрыть окно...")
    except EOFError:
        pass


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv if argv is not None else sys.argv[1:]))
    settings = load_settings()

    paths = list(args.paths)
    if not paths:
        paths = open_p3d_dialog()
    if not paths:
        print("P3D не выбран.")
        pause_if_needed(args, settings)
        return 1

    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    logs_dir = APPDATA_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / (_dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".log")

    print(f"{APP_NAME}")
    print(f"Лог: {log_file}")

    outputs: list[Path] = []
    failures = 0
    try:
        for raw_path in paths:
            try:
                output = process_one_p3d(Path(raw_path), settings, args, log_file)
                if output:
                    outputs.append(output)
            except Exception as exc:
                failures += 1
                log_line(f"ОШИБКА: {exc}", log_file)
        save_settings(settings)
    finally:
        log_line("", log_file)
        log_line(f"Итог: готово {len(outputs)}, ошибок {failures}.", log_file)
        if outputs:
            log_line("Выходные файлы:", log_file)
            for output in outputs:
                log_line(f"  - {output}", log_file)
        pause_if_needed(args, settings)

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
