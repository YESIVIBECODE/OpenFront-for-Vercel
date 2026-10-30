"""Uruchamia lokalne OpenFront i udostępnia je przez tymczasowy tunel Cloudflare."""

from __future__ import annotations

import os
import json
import queue
import re
import shutil
import subprocess
import threading
import time
import webbrowser
from pathlib import Path


def find_game_dir() -> Path:
    """Find the game root beside this launcher or in one of its parent folders."""
    start = Path(__file__).resolve().parent
    for folder in (start, *start.parents):
        if (
            (folder / "package.json").is_file()
            and (folder / "vite.config.ts").is_file()
            and (folder / "src" / "server" / "Server.ts").is_file()
        ):
            return folder
    return start


GAME_DIR = find_game_dir()
LOCAL_URL = "http://localhost:9000"
TUNNEL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.I)
NPM = shutil.which("npm.cmd") or shutil.which("npm")


def node_candidates() -> list[str]:
    candidates = [
        shutil.which("node"),
        str(GAME_DIR / "node.exe"),
        str(GAME_DIR / "runtime" / "node.exe"),
        str(Path(NPM).parent / "node.exe") if NPM else None,
    ]
    # Codex may provide a compatible runtime outside PATH. Search only its
    # known runtime cache, rather than assuming a particular Windows username.
    runtime_cache = Path.home() / ".cache" / "codex-runtimes"
    if runtime_cache.is_dir():
        candidates.extend(
            str(path)
            for path in runtime_cache.glob("*/dependencies/node/bin/node.exe")
        )
    return candidates


def find_supported_node() -> str | None:
    for candidate in node_candidates():
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            version = subprocess.run(
                [candidate, "--version"], capture_output=True, text=True, timeout=5,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        match = re.match(r"^v24\.(\d+)\.", version)
        if match and int(match.group(1)) >= 15:
            return candidate
    return None


NODE = find_supported_node()


def repair_workspace_links() -> bool:
    """Restore npm workspace junctions if a folder copy dereferenced them."""
    workspace_root = GAME_DIR / "node_modules" / "@openfront"
    packages_root = GAME_DIR / "packages"
    if not workspace_root.is_dir() or not packages_root.is_dir():
        return False

    changed = False
    for package_dir in packages_root.iterdir():
        manifest = package_dir / "package.json"
        if not manifest.is_file():
            continue
        try:
            package_name = json.loads(manifest.read_text(encoding="utf-8")).get("name", "")
        except (OSError, json.JSONDecodeError):
            continue
        if not package_name.startswith("@openfront/"):
            continue

        link = workspace_root / package_name.split("/", 1)[1]
        if os.path.lexists(link):
            try:
                if os.path.samefile(link, package_dir):
                    continue
            except OSError:
                pass

            attrs = getattr(link.lstat(), "st_file_attributes", 0)
            is_reparse_point = bool(attrs & 0x400)
            if is_reparse_point:
                if link.is_dir():
                    os.rmdir(link)
                else:
                    link.unlink()
            elif link.is_dir():
                shutil.rmtree(link)
            else:
                link.unlink()

        command = f'mklink /J "{link}" "{package_dir}"'
        result = subprocess.run(
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", command],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Nie udało się naprawić linku pakietu {package_name}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        changed = True
    return changed


def npm_command(*args: str) -> list[str]:
    if not NPM:
        raise RuntimeError("Nie znaleziono npm. Zainstaluj Node.js 24 i npm 12, a potem uruchom plik ponownie.")
    if NPM.lower().endswith(".cmd"):
        # Invoke npm's JS entry point with node so cmd.exe never has to parse
        # npm.cmd paths containing spaces (for example under Program Files).
        npm_cli = Path(NPM).parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
        if npm_cli.is_file() and NODE:
            return [NODE, str(npm_cli), *args]
        command = subprocess.list2cmdline([NPM, *args])
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", f'"{command}"']
    return [NPM, *args]


def stop_process(process: subprocess.Popen | None) -> None:
    if process and process.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main() -> int:
    if not GAME_DIR.is_dir():
        print(f"Nie znaleziono gry: {GAME_DIR}")
        return 1

    if repair_workspace_links():
        print("Naprawiłem lokalne linki pakietów gry.")

    cloudflared = shutil.which("cloudflared")
    if not cloudflared:
        print("Nie znaleziono cloudflared w PATH. Zainstaluj go lub dodaj do PATH.")
        return 1

    if not NODE:
        print("Nie znaleziono Node.js 24. Projekt go wymaga; uruchom ponownie po instalacji Node.js 24.")
        return 1
    if not NPM:
        print("Nie znaleziono npm. Zainstaluj Node.js 24 i npm 12, dodaj je do PATH i uruchom ponownie.")
        return 1

    if not (GAME_DIR / "node_modules").exists():
        answer = input("Brak zależności gry. Zainstalować je teraz (npm run inst)? [T/n] ").strip().lower()
        if answer not in ("", "t", "tak", "y", "yes"):
            return 1
        install_env = os.environ.copy()
        install_env["PATH"] = str(Path(NODE).parent) + os.pathsep + install_env.get("PATH", "")
        result = subprocess.run(npm_command("run", "inst"), cwd=GAME_DIR, env=install_env)
        if result.returncode:
            print("Instalacja zależności nie powiodła się.")
            return result.returncode

    print("Tworzę adres dostępu Cloudflare…", flush=True)
    tunnel = subprocess.Popen(
        [cloudflared, "tunnel", "--no-autoupdate", "--url", LOCAL_URL,
         "--http-host-header", "localhost:9000"],
        cwd=GAME_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    game = None
    try:
        import urllib.request

        lines: queue.Queue[str] = queue.Queue()

        def read_tunnel_output() -> None:
            assert tunnel and tunnel.stdout
            for line in tunnel.stdout:
                lines.put(line.rstrip())

        threading.Thread(target=read_tunnel_output, daemon=True).start()
        public_url = None
        deadline = time.time() + 90
        while time.time() < deadline:
            if tunnel.poll() is not None:
                raise RuntimeError("cloudflared zakończył pracę przed utworzeniem tunelu.")
            try:
                line = lines.get(timeout=0.4)
            except queue.Empty:
                continue
            match = TUNNEL_PATTERN.search(line)
            if match:
                public_url = match.group(0)
                break
        if not public_url:
            raise RuntimeError("Nie udało się uzyskać adresu tunelu w ciągu 90 sekund.")

        host = public_url.removeprefix("https://")
        print("Uruchamiam serwer OpenFront. To może chwilę potrwać…", flush=True)
        env = os.environ.copy()
        env["PATH"] = str(Path(NODE).parent) + os.pathsep + env.get("PATH", "")
        env.update({
            "VITE_HOST": "lan",
            "VITE_PUBLIC_HOST": host,
            "OPENFRONT_SELF_HOSTED": "true",
            "SKIP_BROWSER_OPEN": "true",
        })
        game = subprocess.Popen(npm_command("run", "dev"), cwd=GAME_DIR, env=env)

        server_ready = False
        for _ in range(120):
            if game.poll() is not None:
                raise RuntimeError(f"Serwer gry zakończył pracę z kodem {game.returncode}. Sprawdź komunikaty powyżej.")
            try:
                with urllib.request.urlopen(LOCAL_URL, timeout=1):
                    server_ready = True
                    break
            except Exception:
                time.sleep(1)
        if not server_ready:
            raise RuntimeError("Serwer nie wystartował w ciągu 2 minut. Sprawdź komunikaty powyżej.")

        print("\nOpenFront jest dostępny pod tym adresem:")
        print(f"  {public_url}")
        print("Wyślij ten link bratu. Okno musi pozostać otwarte podczas gry.")
        print("Zamykanie tego okna zatrzyma serwer i tunel.\n")
        webbrowser.open(public_url)

        while game.poll() is None and tunnel.poll() is None:
            time.sleep(0.5)
        if tunnel.poll() is not None:
            raise RuntimeError("Tunel Cloudflare zakończył pracę. Uruchom launcher ponownie.")
        if game.poll() is not None and game.returncode:
            return game.returncode
        return 0
    except KeyboardInterrupt:
        print("\nZatrzymuję OpenFront i tunel…")
        return 0
    except RuntimeError as error:
        print(f"\n{error}")
        return 1
    finally:
        stop_process(game)
        stop_process(tunnel)


if __name__ == "__main__":
    try:
        result = main()
    except KeyboardInterrupt:
        result = 0
    except Exception as error:
        print(f"\nBłąd uruchamiania: {error}")
        result = 1
    input("\nNaciśnij Enter, aby zamknąć to okno…")
    raise SystemExit(result)
