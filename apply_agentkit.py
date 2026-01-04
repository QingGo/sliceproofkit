#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
apply_agentkit.py

Apply "agent-kit" templates into a destination repo.

Key features:
- --agents supports:
  - "all"  (apply all agents listed in manifest)
  - comma-separated list: "antigravity,trae"
- Extensible: available agents are discovered from manifest.yaml, not hard-coded.
- Templates live under src/sliceproofkit/kit; this script copies/renders them into the dev repo.
- Render placeholders in text templates using {{VAR}} (PROJECT_NAME, TODAY, etc).
- Merges .gitignore snippet instead of overwriting.

Usage examples:
  python3 apply_agentkit.py --dest . --agents all
  python3 apply_agentkit.py --dest /path/to/repo --agents antigravity,trae --force
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

# Minimal YAML support:
# - Prefer PyYAML if available; else fallback to a tiny loader for our manifest subset.
try:
    import yaml  # type: ignore
except Exception:
    yaml = None  # noqa: N816

PLACEHOLDER = re.compile(r"\{\{\s*([A-Z0-9_]+)\s*\}\}")

DEFAULT_MANIFEST = "manifest.yaml"
DEFAULT_KIT_REL = Path("src") / "sliceproofkit" / "kit"


@dataclass(frozen=True)
class CopyItem:
    src: Path
    dst: Path
    mode: str = "copy"  # copy | merge_gitignore
    optional: bool = False


def load_manifest(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path}")

    text = path.read_text(encoding="utf-8")

    if yaml is not None:
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("manifest.yaml must be a mapping")
        return data

    # Fallback: extremely small subset parser (enough for the shown manifest)
    # If you don't want PyYAML dependency, keep manifest simple.
    raise RuntimeError(
        "PyYAML not installed. Install with `pip install pyyaml` or run under an env with PyYAML."
    )


def norm_agents_arg(raw: str) -> List[str]:
    raw = raw.strip()
    if not raw:
        return []
    if raw.lower() == "all":
        return ["all"]
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    # de-duplicate while preserving order
    seen = set()
    out = []
    for p in parts:
        if p not in seen:
            out.append(p)
            seen.add(p)
    return out


def render_text(content: str, variables: Dict[str, str]) -> str:
    def repl(m: re.Match) -> str:
        key = m.group(1)
        return variables.get(key, m.group(0))
    return PLACEHOLDER.sub(repl, content)


def is_renderable(path: Path, render_exts: Iterable[str]) -> bool:
    return path.suffix.lower() in set(e.lower() for e in render_exts)


def ensure_parent(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def copy_file(src: Path, dst: Path, *, force: bool, render: bool, variables: Dict[str, str]) -> None:
    ensure_parent(dst)
    if dst.exists() and not force:
        print(f"[skip] {dst}")
        return

    if render:
        # Try treat as UTF-8 text; if fails, fallback to binary copy.
        try:
            txt = src.read_text(encoding="utf-8")
            dst.write_text(render_text(txt, variables), encoding="utf-8")
            shutil.copystat(src, dst, follow_symlinks=True)
            print(f"[render] {dst}")
            return
        except UnicodeDecodeError:
            pass

    shutil.copy2(src, dst)
    print(f"[copy] {dst}")


def copy_tree(src_dir: Path, dst_dir: Path, *, force: bool, render_exts: List[str], variables: Dict[str, str]) -> None:
    if not src_dir.exists():
        raise FileNotFoundError(f"source dir not found: {src_dir}")

    for root, dirs, files in os.walk(src_dir):
        root_p = Path(root)
        rel_root = root_p.relative_to(src_dir)
        target_root = dst_dir / rel_root
        target_root.mkdir(parents=True, exist_ok=True)

        # keep dirs deterministic
        dirs.sort()
        files.sort()

        for fn in files:
            s = root_p / fn
            d = target_root / fn
            copy_file(
                s, d,
                force=force,
                render=is_renderable(s, render_exts),
                variables=variables,
            )


def merge_gitignore(snippet_src: Path, gitignore_dst: Path, *, force: bool) -> None:
    # merge means: append missing lines; never delete existing.
    ensure_parent(gitignore_dst)

    snippet = snippet_src.read_text(encoding="utf-8").splitlines()
    if gitignore_dst.exists():
        existing = gitignore_dst.read_text(encoding="utf-8").splitlines()
    else:
        existing = []

    existing_set = set(existing)
    to_add = [line for line in snippet if line.strip() and line not in existing_set]
    if not to_add:
        print("[merge] .gitignore (no changes)")
        return

    # even if force=True, we still merge instead of overwriting
    with gitignore_dst.open("a", encoding="utf-8") as f:
        if existing and existing[-1] != "":
            f.write("\n")
        f.write("\n".join(to_add) + "\n")

    print(f"[merge] {gitignore_dst} (+{len(to_add)} lines)")


def build_copy_plan(
    kit_root: Path,
    dest_root: Path,
    manifest: Dict[str, Any],
    agents_selected: List[str],
) -> Tuple[List[CopyItem], List[str]]:
    render_exts = manifest.get("render_extensions", [])
    if not isinstance(render_exts, list):
        raise ValueError("render_extensions must be a list")

    agents_map = manifest.get("agents", {})
    if not isinstance(agents_map, dict):
        raise ValueError("agents must be a mapping")

    available_agents = sorted(list(agents_map.keys()))

    if agents_selected == ["all"]:
        chosen = available_agents
    else:
        chosen = agents_selected
        unknown = [a for a in chosen if a not in agents_map]
        if unknown:
            raise ValueError(f"Unknown agents: {unknown}. Available: {available_agents}")

    items: List[CopyItem] = []

    # common copies
    common = manifest.get("common", {})
    common_copy = common.get("copy", [])
    if not isinstance(common_copy, list):
        raise ValueError("common.copy must be a list")

    for it in common_copy:
        items.append(parse_copy_item(kit_root, dest_root, it))

    # agent specific copies
    for agent in chosen:
        agent_block = agents_map[agent]
        copy_list = agent_block.get("copy", [])
        if not isinstance(copy_list, list):
            raise ValueError(f"agents.{agent}.copy must be a list")
        for it in copy_list:
            items.append(parse_copy_item(kit_root, dest_root, it))

    return items, available_agents


def parse_copy_item(kit_root: Path, dest_root: Path, obj: Any) -> CopyItem:
    if not isinstance(obj, dict):
        raise ValueError(f"copy item must be mapping, got: {obj}")

    src = obj.get("src")
    dst = obj.get("dst")
    mode = obj.get("mode", "copy")
    optional = bool(obj.get("optional", False))

    if not isinstance(src, str) or not isinstance(dst, str):
        raise ValueError(f"copy item requires src/dst strings: {obj}")

    return CopyItem(
        src=(kit_root / src).resolve(),
        dst=(dest_root / dst).resolve(),
        mode=str(mode),
        optional=optional,
    )


def apply_plan(
    items: List[CopyItem],
    *,
    force: bool,
    render_exts: List[str],
    variables: Dict[str, str],
) -> None:
    for item in items:
        if item.mode == "merge_gitignore":
            if not item.src.exists():
                if item.optional:
                    print(f"[optional-missing] {item.src}")
                    continue
                raise FileNotFoundError(f"missing snippet: {item.src}")
            merge_gitignore(item.src, item.dst, force=force)
            continue

        # copy mode
        if item.src.is_dir():
            if not item.src.exists():
                if item.optional:
                    print(f"[optional-missing] {item.src}")
                    continue
                raise FileNotFoundError(f"missing dir: {item.src}")
            copy_tree(item.src, item.dst, force=force, render_exts=render_exts, variables=variables)
        else:
            if not item.src.exists():
                if item.optional:
                    print(f"[optional-missing] {item.src}")
                    continue
                raise FileNotFoundError(f"missing file: {item.src}")
            copy_file(
                item.src, item.dst,
                force=force,
                render=is_renderable(item.src, render_exts),
                variables=variables,
            )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True, help="Destination repo path (e.g. .)")
    ap.add_argument(
        "--agents",
        required=True,
        help='Agent selectors: "all" or comma-separated list (e.g. antigravity,trae)',
    )
    default_kit = (Path(__file__).resolve().parent / DEFAULT_KIT_REL).resolve()
    ap.add_argument(
        "--kit",
        default=str(default_kit),
        help="kit root (default: src/sliceproofkit/kit in this repo)",
    )
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST, help="manifest filename (relative to kit root)")
    ap.add_argument("--force", action="store_true", help="Overwrite existing files (gitignore is merged)")
    ap.add_argument(
        "--var",
        action="append",
        default=[],
        help='Extra template vars KEY=VALUE (can repeat). Example: --var ORG=QingGo',
    )
    args = ap.parse_args()

    kit_root = Path(args.kit).resolve()
    dest_root = Path(args.dest).resolve()

    manifest_path = kit_root / args.manifest
    manifest = load_manifest(manifest_path)

    agents_selected = norm_agents_arg(args.agents)

    items, available_agents = build_copy_plan(kit_root, dest_root, manifest, agents_selected)

    # variables for template rendering
    today = dt.datetime.now().strftime("%Y-%m-%d")
    project_name = dest_root.name
    variables: Dict[str, str] = {
        "TODAY": today,
        "PROJECT_NAME": project_name,
    }

    for kv in args.var:
        if "=" not in kv:
            raise ValueError(f"--var must be KEY=VALUE, got: {kv}")
        k, v = kv.split("=", 1)
        variables[k.strip()] = v.strip()

    render_exts = manifest.get("render_extensions", [])
    if not isinstance(render_exts, list):
        raise ValueError("render_extensions must be list")

    print(f"[kit] {kit_root}")
    print(f"[dest] {dest_root}")
    print(f"[agents-available] {', '.join(available_agents)}")
    print(f"[agents-selected] {args.agents}")
    print(f"[force] {args.force}")

    apply_plan(items, force=args.force, render_exts=render_exts, variables=variables)

    # Ensure scripts are executable (in case filesystem lost mode bits)
    scripts_dir = dest_root / "scripts"
    if scripts_dir.exists():
        for p in scripts_dir.glob("*.sh"):
            mode = p.stat().st_mode
            p.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print("\nDone.")
    print("Suggested next steps:")
    print("  1) Edit scripts/verify_fast.sh TODOs to match your stack")
    print("  2) Run: ./scripts/run_with_log.sh verify_fast -- ./scripts/verify_fast.sh")
    print("  3) Start every agent task by reading: AGENT.md")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
