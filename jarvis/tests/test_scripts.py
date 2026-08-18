import os
import subprocess
from pathlib import Path

CLI_ROOT = Path(__file__).resolve().parents[1]


def test_repository_launcher_loads_the_cli_from_its_own_package(tmp_path):
    result = subprocess.run(
        [CLI_ROOT / "scripts" / "aistack", "--version"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "0.2.0"


def test_installer_manages_only_its_symlink(tmp_path):
    install_dir = tmp_path / "bin"
    env = {**os.environ, "AISTACK_INSTALL_DIR": str(install_dir)}
    installer = CLI_ROOT / "scripts" / "install-aistack"

    subprocess.run([installer], env=env, check=True, capture_output=True, text=True)
    command = install_dir / "aistack"
    assert command.is_symlink()
    assert command.readlink() == CLI_ROOT / "scripts" / "aistack"

    result = subprocess.run(
        [command, "--version"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "0.2.0"

    subprocess.run(
        [installer, "--uninstall"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert not command.exists()
