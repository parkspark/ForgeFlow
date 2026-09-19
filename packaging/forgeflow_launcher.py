"""PyInstaller entry point using package-safe absolute imports."""

from forgeflow.main import main

if __name__ == "__main__":
    raise SystemExit(main())
