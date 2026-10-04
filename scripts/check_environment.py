"""S0 환경 확인. 설치·네트워크 접근·전체 환경변수 출력 없음."""
import importlib.metadata
import json
import platform
import shutil
import subprocess


def check_environment():
    result = {
        "system": platform.system(),
        "architecture": platform.machine(),
        "macos_version": platform.mac_ver()[0] or None,
        "python_version": platform.python_version(),
        "tools_on_path": {name: bool(shutil.which(name))
                          for name in ("git", "ffprobe", "ffmpeg")},
        "packages": {},
        "hardware": {},
        "gpu_model_checks": "not_run_S0",
    }
    for name in ("av", "PyYAML"):
        try:
            result["packages"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result["packages"][name] = None
    if platform.system() == "Darwin":
        for key in ("machdep.cpu.brand_string", "hw.memsize"):
            try:
                proc = subprocess.run(["sysctl", "-n", key], capture_output=True,
                                      text=True, timeout=3)
                result["hardware"][key] = (proc.stdout.strip() if proc.returncode == 0
                                           else "unverified_access_denied")
            except (OSError, subprocess.TimeoutExpired):
                result["hardware"][key] = "unverified"
    return result


if __name__ == "__main__":
    print(json.dumps(check_environment(), ensure_ascii=False, indent=2))
