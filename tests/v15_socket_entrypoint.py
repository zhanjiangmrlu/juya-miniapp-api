"""Isolated subprocess only; reuse existing external WeChat/OSS HEAD fixtures."""

import os
import sys

from v15_socket_testkit import PrivateResources, audit_loopback

PrivateResources.from_environment().verify_containers()
sys.addaudithook(audit_loopback)

if os.environ.get("JUYA_SOCKET_TEST_ROLE") == "admin":
    from socket_admin_runtime_app import app
elif os.environ.get("JUYA_SOCKET_TEST_ROLE") == "miniapp":
    from socket_runtime_app import app
else:
    raise RuntimeError("Explicit isolated socket role required")

__all__ = ["app"]
