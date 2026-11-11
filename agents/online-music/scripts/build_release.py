"""Bundle the existing ROS launcher with the new Agent and offline ARM64 dependencies."""

import argparse
import hashlib
import json
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
OLD_PACKAGE = "com_wenzhoujike_k1_music"
NEW_PACKAGE = "com_wenzhoujike_k1_onlinemusic"
MODULE_ROOT = f"agent/{NEW_PACKAGE}/lib/python3.10/site-packages/{NEW_PACKAGE}"


def write(bundle, name, data, mode=0o644):
    # The K1 installer creates directories only from explicit ZIP entries.
    for parent in reversed(PurePosixPath(name).parents):
        if parent == PurePosixPath("."):
            continue
        directory = parent.as_posix() + "/"
        if directory in bundle.NameToInfo:
            continue
        info = zipfile.ZipInfo(directory)
        info.create_system = 3
        info.external_attr = ((stat.S_IFDIR | 0o755) << 16) | 0x10
        bundle.writestr(info, b"")
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    bundle.writestr(info, data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--node-archive", type=Path, required=True)
    parser.add_argument("--node-sha256", required=True)
    parser.add_argument("--wheels", type=Path, required=True)
    args = parser.parse_args()
    if hashlib.sha256(args.node_archive.read_bytes()).hexdigest() != args.node_sha256:
        raise ValueError("Node archive checksum mismatch")
    wheels = sorted(args.wheels.glob("*.whl"))
    if not any(wheel.name.startswith("exceptiongroup-") for wheel in wheels):
        raise ValueError(
            "Python 3.10 requires exceptiongroup. Download its wheel explicitly "
            "when preparing the release from Python 3.11 or newer."
        )
    if (
        not wheels
        or not (
            ROOT / "backend/node_modules/@neteasecloudmusicapienhanced/api"
        ).is_dir()
    ):
        raise ValueError("Prepare ARM64 wheels and run npm ci in backend first")
    manifest = json.loads((ROOT / "agent.json").read_text(encoding="utf-8"))
    output = ROOT / f"{manifest['id']}-{manifest['version']}.unsigned.agent"
    temporary = output.with_suffix(".agent.tmp")
    template = ROOT.parent / "music-player/com.wenzhoujike.k1.music-1.2.0.agent"
    panel = ROOT / "resources/music_panel.zip"
    with zipfile.ZipFile(panel, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted((ROOT / "web").iterdir()):
            bundle.write(path, "dist/" + path.name)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as bundle:
            with zipfile.ZipFile(template) as source:
                for item in source.infolist():
                    if (
                        item.is_dir()
                        or item.filename.startswith(("res/", "resources/"))
                        or item.filename in ("agent.json", "shortcut_list.json")
                    ):
                        continue
                    name = item.filename.replace(OLD_PACKAGE, NEW_PACKAGE)
                    if name.startswith(MODULE_ROOT + "/") or name.endswith(
                        "pyagent_x86_64"
                    ):
                        continue
                    data = source.read(item)
                    try:
                        data = (
                            data.decode("utf-8")
                            .replace(OLD_PACKAGE, NEW_PACKAGE)
                            .encode("utf-8")
                        )
                    except UnicodeDecodeError:
                        pass
                    write(
                        bundle, name, data, (item.external_attr >> 16) & 0o777 or 0o644
                    )
            for path in sorted((ROOT / "src").glob("*.py")):
                write(bundle, MODULE_ROOT + "/" + path.name, path.read_bytes())
            for path in sorted((ROOT / "res").rglob("*")):
                if path.is_file():
                    write(bundle, path.relative_to(ROOT).as_posix(), path.read_bytes())
            write(bundle, "agent.json", (ROOT / "agent.json").read_bytes())
            write(bundle, "LICENSE", (ROOT / "LICENSE").read_bytes())
            write(
                bundle, "shortcut_list.json", (ROOT / "shortcut_list.json").read_bytes()
            )
            write(bundle, "resources/music_panel.zip", panel.read_bytes())
            for path in sorted((ROOT / "backend").rglob("*")):
                if path.is_file():
                    write(
                        bundle,
                        "resources/netease/"
                        + path.relative_to(ROOT / "backend").as_posix(),
                        path.read_bytes(),
                    )
            for wheel in wheels:
                with zipfile.ZipFile(wheel) as source:
                    for item in source.infolist():
                        if item.is_dir():
                            continue
                        path = Path(item.filename)
                        if (
                            path.is_absolute()
                            or ".." in path.parts
                            or ".data/" in item.filename
                        ):
                            raise ValueError(
                                f"Unsupported wheel entry: {item.filename}"
                            )
                        write(
                            bundle,
                            "resources/python/" + item.filename,
                            source.read(item),
                        )
            with tarfile.open(args.node_archive) as source:
                for member in source.getmembers():
                    if member.isfile() and member.name.endswith(
                        ("/bin/node", "/LICENSE")
                    ):
                        parts = Path(member.name).parts[1:]
                        if parts not in (("bin", "node"), ("LICENSE",)):
                            continue
                        write(
                            bundle,
                            "resources/netease/node/" + "/".join(parts),
                            source.extractfile(member).read(),
                            0o755 if parts[-1] == "node" else 0o644,
                        )
            write(
                bundle,
                "resources/dependencies.json",
                json.dumps(
                    {
                        "python_wheels": [wheel.name for wheel in wheels],
                        "node_archive": args.node_archive.name,
                        "node_sha256": args.node_sha256,
                        "netease_api": "4.41.1",
                    },
                    indent=2,
                ).encode(),
            )
        with zipfile.ZipFile(temporary) as bundle:
            if bundle.testzip() is not None:
                raise ValueError("Archive validation failed")
            assert bundle.read("agent.json") == (ROOT / "agent.json").read_bytes()
            assert (
                bundle.getinfo("resources/netease/node/bin/node").external_attr >> 16
                & 0o111
            )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Created {output} ({output.stat().st_size / 1024 / 1024:.1f} MiB)")


if __name__ == "__main__":
    main()
