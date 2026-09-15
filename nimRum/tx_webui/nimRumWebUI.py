#!/usr/bin/env python3
"""nimRumWebUI — thin wrapper for backward compatibility.

The actual implementation lives in nimRum.tx_webui.app.
This module exists so that the pyproject.toml entry point
(runNimRumWebUI = "nimRum.tx_webui.nimRumWebUI:main") continues to work.
"""

from nimRum.tx_webui.app import main  # noqa: F401

if __name__ == "__main__":
    main()
