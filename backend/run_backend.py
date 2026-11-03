"""PyInstaller entry point for the packaged backend (mmi-backend.exe)."""

import multiprocessing

from mmi.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # worker processes re-enter here in the frozen app
    main()
