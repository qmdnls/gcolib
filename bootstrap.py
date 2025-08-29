import argparse, json, os, sys, subprocess, pathlib, textwrap
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import urllib.request

BASE = pathlib.Path("/content")
REPO_ROOT = BASE / "repos"
REPO_ROOT.mkdir(parents=True, exist_ok=True)

@dataclass
class RepoSpec:
    name: str
    url: str
    ref: str = "main"
    editable: bool = True
    submodules: bool = False
    extras: List[str] = field(default_factory=list)
    install: str = "pip"     # pip | uv | poetry | none
    path: str = "."          # subdir with pyproject/setup
    env: Dict[str, str] = field(default_factory=dict)
    post_install: Optional[str] = None

def run(cmd, cwd=None, env=None, quiet=False, check=True):
    if not quiet:
        print("$", " ".join(cmd))
    p = subprocess.run(cmd, cwd=cwd, env=env or os.environ.copy(),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if not quiet:
        print(p.stdout)
    if check and p.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd)}")
    return p

def ensure(module: str, pip_name: Optional[str] = None):
    try:
        __import__(module)
    except Exception:
        run([sys.executable, "-m", "pip", "install", "-q", pip_name or module])

def load_manifest(path_or_url: str) -> dict:
    import yaml
    if path_or_url.startswith(("http://", "https://")):
        with urllib.request.urlopen(path_or_url) as r:
            data = r.read().decode("utf-8")
    else:
        data = pathlib.Path(path_or_url).read_text()
    return yaml.safe_load(data)

def git_clone_or_fetch(spec: RepoSpec, gh_token: Optional[str]):
    dest = REPO_ROOT / spec.name
    if dest.exists():
        run(["git", "fetch", "--all", "--tags"], cwd=dest)
    else:
        clone_cmd = ["git", "clone", spec.url, str(dest)]
        if gh_token and spec.url.startswith("https://github.com/"):
            clone_cmd = ["git", "-c", f"http.extraheader=AUTHORIZATION: bearer {gh_token}",
                         "clone", spec.url, str(dest)]
        run(clone_cmd)
    run(["git", "checkout", spec.ref], cwd=dest)
    if spec.submodules:
        run(["git", "submodule", "update", "--init", "--recursive"], cwd=dest)
    return dest

def pip_editable_install(project_dir: pathlib.Path, extras: List[str]):
    egg = str(project_dir) + (f"[{','.join(extras)}]" if extras else "")
    run([sys.executable, "-m", "pip", "install", "-q", "-U", "pip"], quiet=True)
    run([sys.executable, "-m", "pip", "install", "-e", egg])

def pip_noneditable_install(project_dir: pathlib.Path, extras: List[str]):
    target = "." + (f"[{','.join(extras)}]" if extras else "")
    run([sys.executable, "-m", "pip", "install", "-q", "-U", "pip"], quiet=True)
    run([sys.executable, "-m", "pip", "install", target], cwd=project_dir)

def uv_install(project_dir: pathlib.Path, editable: bool, extras: List[str]):
    ensure("uv")
    if editable:
        pip_editable_install(project_dir, extras)
    else:
        target = "."
        if extras:
            target = f".[{','.join(extras)}]"
        run([sys.executable, "-m", "uv", "pip", "install", target], cwd=project_dir)

def poetry_install(project_dir: pathlib.Path, editable: bool, extras: List[str]):
    ensure("poetry")
    args = [sys.executable, "-m", "poetry", "install"]
    if extras:
        args += ["--with", ",".join(extras)]
    run(args, cwd=project_dir)
    if editable:
        add_to_syspath(project_dir)  # poetry has no true -e

def add_to_syspath(project_dir: pathlib.Path):
    p = str(project_dir)
    if p not in sys.path:
        sys.path.insert(0, p)

def install_repo(spec: RepoSpec, repo_dir: pathlib.Path):
    project_dir = (repo_dir / spec.path).resolve()
    os.environ.update(spec.env)
    if spec.install == "none":
        add_to_syspath(project_dir)
    elif spec.install == "pip":
        (pip_editable_install if spec.editable else pip_noneditable_install)(project_dir, spec.extras)
    elif spec.install == "uv":
        uv_install(project_dir, spec.editable, spec.extras)
    elif spec.install == "poetry":
        poetry_install(project_dir, spec.editable, spec.extras)
    else:
        raise SystemExit(f"unknown install mode: {spec.install}")
    add_to_syspath(project_dir)
    if spec.post_install:
        run(["bash", "-lc", spec.post_install], cwd=project_dir)
    return {"name": spec.name, "path": str(project_dir)}

def enable_autoreload():
    try:
        ip = get_ipython()  # noqa
        ip.run_line_magic("load_ext", "autoreload")
        ip.run_line_magic("autoreload", "2")
    except Exception:
        pass

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(pathlib.Path(__file__).with_name("default.yaml")))
    ap.add_argument("--gh-token", default=os.environ.get("GH_TOKEN", ""))
    ap.add_argument("--mount-drive", action="store_true", help="mount google drive and cache repos there")
    args = ap.parse_args()

    if args.mount_drive:
        # lightweight, avoids import failures if not in colab
        try:
            from google.colab import drive  # type: ignore
            drive.mount("/content/drive", force_remount=True)
            global REPO_ROOT
            REPO_ROOT = pathlib.Path("/content/drive/MyDrive/gcolab_repos")
            REPO_ROOT.mkdir(parents=True, exist_ok=True)
            print(f"using repo cache at {REPO_ROOT}")
        except Exception as e:
            print("drive mount failed or not in colab:", e)

    manifest = load_manifest(args.manifest)
    specs = [RepoSpec(**e) for e in manifest.get("repos", [])]
    installed = []
    for spec in specs:
        repo_dir = git_clone_or_fetch(spec, args.gh_token or None)
        installed.append(install_repo(spec, repo_dir))
    enable_autoreload()
    print("installed repos:", json.dumps(installed, indent=2))
    print("done. you can now import your packages.")

if __name__ == "__main__":
    ensure("yaml", "pyyaml")
    main()
