#!/usr/bin/env python3
"""
Copyright (c) 2026 xqtdk

This software is provided 'as-is', without any express or implied warranty.
In no event will the authors be held liable for any damages arising from the use of this software.

Permission is granted to anyone to use this software for any purpose,
including commercial applications, and to alter it and redistribute it
freely, subject to the following restrictions:

   1. The origin of this software must not be misrepresented; you must not
      claim that you wrote the original software. If you use this software
      in a product, an acknowledgment in the product documentation would be
      appreciated but is not required.

   2. Altered source versions must be plainly marked as such, and must not be
      misrepresented as being the original software.

   3. This notice may not be removed or altered from any source distribution.
"""

from __future__ import annotations

"""
Minecraftのmodフォルダを再帰的にスキャンし、
.jarを直接含む各ディレクトリに list.toml を生成するスクリプト。

TOMLヘッダーキーの形式:
    [["プロジェクト名/ディレクトリ/サブディレクトリ"]]

ルートには全TOMLをまとめた mods_index.yaml を生成する。

カテゴリ分類 (ディレクトリ名で自動判定):
    active       - 使用中 / 有効 / その他(デフォルト: 上位ディレクトリ直下)
    deleted      - deleted / delete / 削除
    update       - update / updates / アプデ無し
    bug          - バグ / バグ？ / bug / bugs
    ysm          - ysm (Yes Steve Model)
    resourcepacks - resourcepack / resourcepacks / リソースパック 等
    shaderpacks  - shaderpack / shaderpacks / shader 等
    optional     - オプション / その他のサブディレクトリ

使い方:
    python scripts/generate_mod_list.py [--no-git] [--path DIR] [--verbose]

オプション:
    --no-git   Gitへの自動コミットをスキップする
    --path DIR スキャン対象のルートディレクトリ (デフォルト: .)
    --verbose  詳細なログを表示する
"""

import argparse
import hashlib
import json
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from typing import TypedDict

import tomli_w
import yaml
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table
from rich.panel import Panel

# TOMLの読み込みは Python 3.11+ 標準の tomllib を使用し、
# それ以前のバージョンでは tomli にフォールバックする
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

# =========================================================
# グローバル
# =========================================================

console = Console()

# =========================================================
# 定数
# =========================================================

BINARY_EXTENSIONS: frozenset[str] = frozenset({".jar", ".zip"})

DELETED_DIR_NAMES: frozenset[str] = frozenset({"deleted", "delete", "削除"})
UPDATE_DIR_NAMES: frozenset[str] = frozenset({"update", "updates", "アプデ無し"})
BUG_DIR_NAMES: frozenset[str] = frozenset({"バグ", "バグ？", "bug", "bugs"})
YSM_DIR_NAMES: frozenset[str] = frozenset({"ysm"})
RESOURCEPACKS_DIR_NAMES: frozenset[str] = frozenset({
    "resourcepack", "resourcepacks",
    "リソースパック", "必須リソースパック",
})
SHADERPACKS_DIR_NAMES: frozenset[str] = frozenset({
    "shaderpack", "shaderpacks",
    "shader", "shaders",
    "shader_bak", "shaderpacks_old", "shaderpacks_backup",
})
# 明示的にactiveと判定する日本語フォルダ名
ACTIVE_DIR_NAMES: frozenset[str] = frozenset({"使用中", "有効"})
# 明示的にoptionalと判定する日本語フォルダ名
OPTIONAL_DIR_NAMES_EXTRA: frozenset[str] = frozenset({"オプション"})

EXCLUDED_DIR_NAMES: frozenset[str] = frozenset({".git", "__pycache__", ".venv", ".gemini"})

# サブフォルダ数がこの値を超えるディレクトリは後回し候補とする
DEFAULT_SUBDIR_LIMIT: int = 10

OUTPUT_FILENAME = "list.toml"
INDEX_FILENAME = "mods_index.yaml"
HASH_CHUNK_SIZE = 8 * 1024 * 1024

# カテゴリの日本語表示名マッピング
CATEGORY_LABELS: dict[str, str] = {
    "active": "有効",
    "deleted": "削除済み",
    "update": "アップデート待ち",
    "bug": "バグあり",
    "ysm": "YSM",
    "resourcepacks": "リソースパック",
    "shaderpacks": "シェーダーパック",
    "optional": "オプション",
}

# =========================================================
# 型定義
# =========================================================

class ModEntry(TypedDict):
    name: str
    mod_name: str
    mod_version: str
    mod_url: str
    size_readable: str
    sha256: str
    modified_at: str


class FolderData(TypedDict):
    folder_path: str      # プロジェクトルートからの相対パス (例: "個人的mod/opt")
    category: str         # カテゴリ名
    mods: list[ModEntry]  # このディレクトリ内のmodエントリ一覧

# =========================================================
# ユーティリティ関数
# =========================================================

def format_file_size(size_bytes: int) -> str:
    """ファイルサイズを人間が読みやすい形式に変換する"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    else:
        return f"{size_bytes / 1024 ** 3:.2f} GB"


def compute_sha256(file_path: Path) -> str:
    """ファイルのSHA-256ハッシュを計算して16進数文字列で返す"""
    h = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(HASH_CHUNK_SIZE):
                h.update(chunk)
    except OSError as e:
        console.print(f"[bold red][エラー] ファイルの読み取りに失敗しました: {file_path}: {e}[/bold red]")
        return ""
    return h.hexdigest()


def _sanitize_version(version: str) -> str:
    """ビルドシステムのプレースホルダーを空文字に置き換える"""
    if not version or version.startswith("${"):
        return ""
    return version


def extract_jar_metadata(jar_path: Path) -> tuple[str, str, str]:
    """JARファイル内のメタデータファイルからmod情報を取得する"""
    if jar_path.suffix.lower() != ".jar":
        return ("", "", "")

    try:
        with zipfile.ZipFile(jar_path, "r") as zf:
            names_lower = {n.lower(): n for n in zf.namelist()}

            # Forge / NeoForge
            toml_key = "meta-inf/mods.toml"
            if toml_key in names_lower and tomllib is not None:
                try:
                    raw = zf.read(names_lower[toml_key])
                    data = tomllib.loads(raw.decode("utf-8", errors="replace"))
                    mods_list = data.get("mods", [])
                    if mods_list:
                        mod = mods_list[0]
                        name    = mod.get("displayName", "")
                        version = _sanitize_version(mod.get("version", ""))
                        url     = mod.get("displayURL", "")
                        if not version:
                            manifest_key = "meta-inf/manifest.mf"
                            if manifest_key in names_lower:
                                manifest = zf.read(names_lower[manifest_key]).decode(
                                    "utf-8", errors="replace"
                                )
                                for line in manifest.splitlines():
                                    if line.lower().startswith("implementation-version:"):
                                        version = line.split(":", 1)[1].strip()
                                        break
                        return (name, version, url)
                except Exception:
                    pass

            # Fabric / Quilt
            fabric_key = "fabric.mod.json"
            if fabric_key in names_lower:
                try:
                    raw = zf.read(names_lower[fabric_key])
                    data = json.loads(raw.decode("utf-8", errors="replace"))
                    name    = data.get("name", data.get("id", ""))
                    version = _sanitize_version(data.get("version", ""))
                    url     = data.get("contact", {}).get("homepage", "")
                    return (name, version, url)
                except Exception:
                    pass

            # Legacy Forge
            mcmod_key = "mcmod.info"
            if mcmod_key in names_lower:
                try:
                    raw = zf.read(names_lower[mcmod_key])
                    data = json.loads(raw.decode("utf-8", errors="replace"))
                    if isinstance(data, list) and data:
                        mod = data[0]
                    elif isinstance(data, dict):
                        mod = data.get("modList", [{}])[0] if "modList" in data else data
                    else:
                        mod = {}
                    name    = mod.get("name", "")
                    version = _sanitize_version(mod.get("version", ""))
                    url     = mod.get("url", mod.get("homepage", ""))
                    return (name, version, url)
                except Exception:
                    pass

    except (zipfile.BadZipFile, OSError):
        pass

    return ("", "", "")


def build_mod_entry(file_path: Path) -> ModEntry | None:
    """ファイルパスからModEntryを生成する"""
    try:
        stat = file_path.stat()
        sha256 = compute_sha256(file_path)
        if not sha256:
            return None
        mod_name, mod_version, mod_url = extract_jar_metadata(file_path)

        return ModEntry(
            name=file_path.name,
            mod_name=mod_name,
            mod_version=mod_version,
            mod_url=mod_url,
            size_readable=format_file_size(stat.st_size),
            sha256=sha256,
            modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
        )
    except Exception as e:
        console.print(f"[yellow]  [警告] 処理に失敗しました: {file_path.name}: {e}[/yellow]")
        return None


def classify_dir(dir_name: str) -> str:
    """ディレクトリ名からカテゴリ名を返す"""
    # 英語は小文字化、日本語はそのままで両方チェックする
    lower = dir_name.lower()
    if lower in DELETED_DIR_NAMES or dir_name in DELETED_DIR_NAMES:
        return "deleted"
    if lower in UPDATE_DIR_NAMES or dir_name in UPDATE_DIR_NAMES:
        return "update"
    if lower in BUG_DIR_NAMES or dir_name in BUG_DIR_NAMES:
        return "bug"
    if lower in YSM_DIR_NAMES or dir_name in YSM_DIR_NAMES:
        return "ysm"
    if lower in RESOURCEPACKS_DIR_NAMES or dir_name in RESOURCEPACKS_DIR_NAMES:
        return "resourcepacks"
    if lower in SHADERPACKS_DIR_NAMES or dir_name in SHADERPACKS_DIR_NAMES:
        return "shaderpacks"
    if dir_name in ACTIVE_DIR_NAMES:
        return "active"
    if dir_name in OPTIONAL_DIR_NAMES_EXTRA:
        return "optional"
    # 上記に該当しない場合はoptional扱い(サブディレクトリのデフォルト)
    return "optional"

# =========================================================
# フォルダ探索
# =========================================================

def load_gitignore_dirs(project_root: Path) -> frozenset[str]:
    """.gitignoreからディレクトリ除外パターンを読み込む。

    簡易実装: 以下のパターンのみ対応する。
    - 末尾に / がある行 (shader-test/ 等)
    - サブディレクトリ / を含まないシンプルな名前
    - ネゲーション (!) ・コメント・ファイルグロブ (*.jar 等) は無視する。
    """
    gitignore_path = project_root / ".gitignore"
    if not gitignore_path.exists():
        return frozenset()

    dirs: set[str] = set()
    try:
        content = gitignore_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return frozenset()

    for raw_line in content.splitlines():
        line = raw_line.strip()
        # コメント・空行・ネゲーション・ファイルグロブはスキップ
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        if line.startswith("*"):  # *.jar 等のファイルパターンは無視
            continue
        # 末尾の / を除去してディレクトリ名として登録
        name = line.rstrip("/")
        # サブパスパターン (foo/bar) は複雑なのでスキップ
        if "/" in name:
            continue
        if name:
            dirs.add(name)

    if dirs:
        console.print(
            f"[dim].gitignore から {len(dirs)} 件の除外ディレクトリを読み込みました: "
            + ", ".join(sorted(dirs))
            + "[/dim]"
        )

    return frozenset(dirs)

def _walk_dirs(
    start_dirs: list[Path],
    result: list[Path],
    deferred: list[Path] | None,
    subdir_limit: int | None,
    excluded: frozenset[str],
) -> None:
    """再帰的にmodフォルダを探す内部共通関数。
    subdir_limit が None の場合はサブフォルダ数チェックを行わない。
    """
    for directory in start_dirs:
        if directory.name.lower() in excluded or directory.name in excluded:
            continue

        has_jars = False
        subdirs: list[Path] = []
        try:
            for item in sorted(directory.iterdir()):
                if item.is_file() and item.suffix.lower() in BINARY_EXTENSIONS:
                    has_jars = True
                elif item.is_dir() and item.name.lower() not in excluded and item.name not in excluded:
                    subdirs.append(item)
        except PermissionError:
            pass

        if has_jars:
            result.append(directory)

        # サブフォルダ数が閾値を超えたら後回しリストに追加して探索を中断する
        if subdir_limit is not None and len(subdirs) > subdir_limit:
            if deferred is not None:
                deferred.append(directory)
            continue

        _walk_dirs(subdirs, result, deferred, subdir_limit, excluded)


def find_mod_folders(
    root: Path,
    subdir_limit: int | None = None,
    excluded: frozenset[str] = EXCLUDED_DIR_NAMES,
) -> tuple[list[Path], list[Path]]:
    """プロジェクトルート以下で .jar/.zip を直接含むディレクトリを全て探す。

    Args:
        root: スキャン対象のルートディレクトリ
        subdir_limit: サブフォルダ数の上限。超えた場合は deferred に追加して探索を後回しにする。
                      None の場合は無制限。
        excluded: 除外するディレクトリ名のセット。

    Returns:
        (mod_folders, deferred_dirs) のタプル。
        deferred_dirs はサブフォルダ数が上限を超えたディレクトリの一覧。
    """
    mod_folders: list[Path] = []
    deferred_dirs: list[Path] = []

    try:
        start = [
            c for c in sorted(root.iterdir())
            if c.is_dir() and c.name.lower() not in excluded and c.name not in excluded
        ]
    except PermissionError:
        start = []

    _walk_dirs(start, mod_folders, deferred_dirs, subdir_limit, excluded)
    return mod_folders, deferred_dirs


def find_mod_folders_in(
    directories: list[Path],
    excluded: frozenset[str] = EXCLUDED_DIR_NAMES,
) -> list[Path]:
    """指定したディレクトリ以下のmodフォルダをサブフォルダ数制限なしで全て探す。
    後回しにしたディレクトリを処理する際に使用する。
    """
    result: list[Path] = []
    _walk_dirs(directories, result, None, None, excluded)
    return result

# =========================================================
# スキャン
# =========================================================

def scan_folder(
    folder: Path,
    project_root: Path,
    progress: Progress,
    verbose: bool = False,
) -> FolderData:
    """1つのディレクトリをスキャンしてFolderDataを返す。
    直接含まれる .jar/.zip のみを対象とし、サブディレクトリ内は対象外。
    """
    rel = folder.relative_to(project_root)

    # カテゴリはフォルダ名で判定。プロジェクトルート直下の最初の階層は active とする
    if len(rel.parts) <= 1:
        category = "active"
    else:
        category = classify_dir(folder.name)

    # 直下のjarファイル一覧を取得
    items: list[Path] = []
    try:
        items = sorted(
            item for item in folder.iterdir()
            if item.is_file() and item.suffix.lower() in BINARY_EXTENSIONS
        )
    except PermissionError:
        pass

    task_id = progress.add_task(f"スキャン中: [bold]{folder.name}[/bold]", total=len(items))
    mods: list[ModEntry] = []

    for item in items:
        progress.update(task_id, description=f"処理中 [cyan]{item.name}[/cyan]")
        entry = build_mod_entry(item)
        if entry:
            mods.append(entry)
            if verbose:
                console.print(f"  [dim]{item.name}[/dim]")
        progress.advance(task_id)

    folder_path = str(rel).replace("\\", "/")

    return FolderData(
        folder_path=folder_path,
        category=category,
        mods=mods,
    )

# =========================================================
# TOML書き出し
# =========================================================

def write_list_toml(output_path: Path, data: FolderData, project_root: Path) -> None:
    """list.tomlを書き出す。
    TOMLヘッダーキー形式: [["プロジェクト名/フォルダパス"]]
    """
    project_name = project_root.name
    table_key = f"{project_name}/{data['folder_path']}"

    # tomli_w はキーに / や特殊文字が含まれる場合、自動でクォートする
    toml_data = {table_key: [dict(entry) for entry in data["mods"]]}

    with open(output_path, "wb") as f:
        tomli_w.dump(toml_data, f)

# =========================================================
# YAMLインデックス生成
# =========================================================

def generate_index_yaml(
    project_root: Path,
    all_data: list[FolderData],
    toml_paths: list[Path],
) -> Path:
    """プロジェクトルートに mods_index.yaml を生成する。
    PyYAML を使用して構造化された YAML を生成する。
    """
    project_name = project_root.name
    total_mods = sum(len(d["mods"]) for d in all_data)

    index = {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "total_mods": total_mods,
        "folders": [
            {
                "path": f"{project_name}/{d['folder_path']}",
                "toml": str(toml_path.relative_to(project_root)).replace("\\", "/"),
                "category": d["category"],
                "count": len(d["mods"]),
            }
            for d, toml_path in zip(all_data, toml_paths)
        ],
    }

    output_path = project_root / INDEX_FILENAME
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(
            index,
            f,
            allow_unicode=True,        # 日本語パスをエスケープせずに出力する
            sort_keys=False,           # キーの順序を元の辞書の順序に保つ
            default_flow_style=False,  # ブロックスタイルで出力する
        )
    return output_path

# =========================================================
# Git
# =========================================================

def git_commit_files(project_root: Path, paths: list[Path]) -> None:
    """生成したファイルをGitにコミットする"""
    if not paths:
        return

    console.print("\n[bold blue]Git: 変更をコミットしています...[/bold blue]")

    try:
        subprocess.run(["git", "--version"], cwd=project_root, check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        console.print("[yellow][警告] git コマンドが見つかりません。コミットをスキップします。[/yellow]")
        return

    # 対象ファイルのみの変更有無を確認する (Windowsのcp932エラー回避のためtext=False)
    path_strs = [str(p) for p in paths]
    status = subprocess.run(
        ["git", "status", "--porcelain", "--"] + path_strs,
        cwd=project_root,
        capture_output=True,
        text=False,
    )
    stdout_text = (status.stdout or b"").decode("utf-8", errors="replace")
    if not stdout_text.strip():
        console.print("[green]変更はありませんでした。コミットをスキップします。[/green]")
        return

    for path in paths:
        subprocess.run(["git", "add", str(path)], cwd=project_root, check=True, capture_output=True)

    commit_message = f"docs: mod一覧を更新 ({datetime.now().strftime('%Y-%m-%d %H:%M')})"
    try:
        subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=False,
        )
        console.print(f"[bold green]OK[/bold green] コミット完了: {commit_message}")
    except subprocess.CalledProcessError as err:
        stderr = err.stderr.decode("utf-8", errors="replace") if err.stderr else ""
        console.print(f"[red][エラー] git commit に失敗しました: {stderr.strip()}[/red]")

# =========================================================
# メイン
# =========================================================

def _process_folders(
    mod_folders: list[Path],
    project_root: Path,
    verbose: bool,
    generated_tomls: list[Path],
    all_folder_data: list[FolderData],
) -> None:
    """フォルダ一覧をスキャンしてlist.tomlを生成する共通処理"""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        for mod_folder in mod_folders:
            data = scan_folder(mod_folder, project_root, progress, verbose=verbose)
            output_path = mod_folder / OUTPUT_FILENAME
            try:
                write_list_toml(output_path, data, project_root)
                generated_tomls.append(output_path)
                all_folder_data.append(data)
            except OSError as err:
                console.print(f"[red][エラー] TOMLの書き込みに失敗しました: {output_path}: {err}[/red]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Minecraft Mod リスト生成ツール")
    parser.add_argument("--no-git", action="store_true", help="Gitへの自動コミットをスキップする")
    parser.add_argument("--path", type=str, default=".", help="スキャン対象のルートディレクトリ")
    parser.add_argument("--verbose", action="store_true", help="詳細なログを表示する")
    parser.add_argument(
        "--subdir-limit",
        type=int,
        default=DEFAULT_SUBDIR_LIMIT,
        metavar="N",
        help=f"サブフォルダ数がN件を超えるディレクトリを後回しにする (デフォルト: {DEFAULT_SUBDIR_LIMIT}、0で無効化)",
    )
    args = parser.parse_args()

    project_root = Path(args.path).resolve()
    subdir_limit: int | None = args.subdir_limit if args.subdir_limit > 0 else None

    # .gitignore からディレクトリ除外パターンを読み込んで定数と統合する
    gitignore_dirs = load_gitignore_dirs(project_root)
    excluded_dirs: frozenset[str] = EXCLUDED_DIR_NAMES | gitignore_dirs

    console.print(Panel(
        f"[bold green]Minecraft Mod リスト生成ツール[/bold green]\nスキャン対象: {project_root}",
        expand=False,
    ))

    if tomllib is None:
        console.print(
            "[bold red][警告][/bold red] tomllib/tomli が見つかりません。"
            "Forge/NeoForge のメタデータ取得には Python 3.11+ または 'pip install tomli' が必要です。"
        )

    mod_folders, deferred_dirs = find_mod_folders(project_root, subdir_limit, excluded=excluded_dirs)
    if not mod_folders and not deferred_dirs:
        console.print("[yellow]modフォルダが見つかりませんでした。[/yellow]")
        return

    generated_tomls: list[Path] = []
    all_folder_data: list[FolderData] = []

    _process_folders(mod_folders, project_root, args.verbose, generated_tomls, all_folder_data)

    # サブフォルダが多いため後回しにしたディレクトリをフォルダごとに確認する
    if deferred_dirs:
        console.print()
        console.print(
            f"[bold yellow]⚠ サブフォルダが {subdir_limit} 件を超えるディレクトリがあります。"
            "1つずつ処理するか確認します:[/bold yellow]"
        )

        confirmed_dirs: list[Path] = []
        for d in deferred_dirs:
            try:
                sub_count = sum(
                    1 for c in d.iterdir()
                    if c.is_dir() and c.name.lower() not in excluded_dirs and c.name not in excluded_dirs
                )
            except PermissionError:
                sub_count = 0
            rel = str(d.relative_to(project_root)).replace("\\", "/")
            console.print()
            console.print(f"  [cyan]{rel}[/cyan] ({sub_count} サブフォルダ)")
            try:
                answer = console.input(
                    "  [bold]このフォルダを処理しますか？ (y/N): [/bold]"
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer in ("y", "yes"):
                confirmed_dirs.append(d)
            else:
                console.print("  [dim]スキップしました。[/dim]")

        if confirmed_dirs:
            extra_folders = find_mod_folders_in(confirmed_dirs, excluded=excluded_dirs)
            if extra_folders:
                console.print(f"\n[blue]{len(extra_folders)} 件のフォルダを追加処理します...[/blue]")
                _process_folders(extra_folders, project_root, args.verbose, generated_tomls, all_folder_data)
            else:
                console.print("[yellow]追加のmodフォルダは見つかりませんでした。[/yellow]")
            console.print("[dim]後回しフォルダをスキップしました。[/dim]")

    # YAMLインデックスを生成する
    try:
        yaml_path = generate_index_yaml(project_root, all_folder_data, generated_tomls)
        console.print(f"[bold green]OK[/bold green] インデックスYAML: {yaml_path.relative_to(project_root)}")
    except OSError as err:
        console.print(f"[red][エラー] YAMLの書き込みに失敗しました: {err}[/red]")
        yaml_path = None

    # サマリーテーブルの表示
    table = Table(title="スキャン結果サマリー")
    table.add_column("フォルダ", style="cyan")
    table.add_column("カテゴリ", style="magenta")
    table.add_column("件数", justify="right", style="green")

    total_mods = 0
    for data in all_folder_data:
        count = len(data["mods"])
        total_mods += count
        label = CATEGORY_LABELS.get(data["category"], data["category"])
        table.add_row(data["folder_path"], label, str(count))

    console.print(table)
    console.print(
        f"\n[bold green]完了![/bold green] "
        f"{len(generated_tomls)} フォルダに {OUTPUT_FILENAME} を生成しました。"
        f" (合計 {total_mods} 件)"
    )

    if not args.no_git:
        commit_targets = list(generated_tomls)
        if yaml_path:
            commit_targets.append(yaml_path)
        console.print()
        try:
            answer = console.input(
                "[bold]変更をGitにコミットしますか？ (y/N): [/bold]"
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer in ("y", "yes"):
            git_commit_files(project_root, commit_targets)
        else:
            console.print("[dim]コミットをスキップしました。[/dim]")


if __name__ == "__main__":
    main()
