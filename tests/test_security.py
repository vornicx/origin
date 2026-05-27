"""Security regression tests — path traversal, password policy, GDPR endpoints."""

import pytest
import tempfile
import os


class TestPasswordPolicy:
    def test_register_rejects_short_password(self):
        from core.auth import register_user
        import time

        u = f"sec_test_{int(time.time())}"
        with pytest.raises(Exception, match="12"):
            register_user(u, "short")

    def test_register_rejects_11_char_password(self):
        from core.auth import register_user
        import time

        u = f"sec_test_{int(time.time())}_2"
        with pytest.raises(Exception):
            register_user(u, "11charpass!")  # exactly 11 chars

    def test_register_accepts_12_char_password(self):
        from core.auth import register_user
        import time

        u = f"sec_test_{int(time.time())}_3"
        result = register_user(u, "12charpass!X")  # exactly 12 chars
        assert result["username"] == u

    def test_min_password_length_constant(self):
        from core.auth import MIN_PASSWORD_LENGTH
        assert MIN_PASSWORD_LENGTH >= 12


class TestPathTraversal:
    def test_safe_filename_strips_traversal(self):
        from core.context_manager import ContextManager

        assert ContextManager._safe_filename("../../etc/passwd") == "passwd"
        assert ContextManager._safe_filename("../secrets.md") == "secrets.md"
        assert ContextManager._safe_filename("normal.md") == "normal.md"

    def test_safe_filename_strips_backslash_traversal(self):
        from core.context_manager import ContextManager

        assert ContextManager._safe_filename("..\\..\\windows\\system32") == "system32"

    def test_safe_filename_rejects_empty(self):
        from core.context_manager import ContextManager

        with pytest.raises(ValueError):
            ContextManager._safe_filename("")

    def test_set_context_sanitizes_filename(self):
        from core.context_manager import ContextManager

        cm = ContextManager()
        cm.set_context("../evil.md", "content")
        assert "evil.md" in cm._files
        assert "../evil.md" not in cm._files


class TestMemoryGDPR:
    def test_clear_all_removes_memories(self):
        from core.memory_manager import MemoryManager

        mm = MemoryManager.__new__(MemoryManager)
        mm.memories = {"id1": object(), "id2": object()}
        mm.conversation_history = [{"role": "user", "content": "hello"}]
        mm.preferences = {"key": "value"}
        mm.conversation_summaries = []
        mm._chroma = type("_NullChroma", (), {"is_ready": False})()

        import pathlib
        # Point files to temp paths so we don't break real data
        import core.memory_manager as mmmod
        original_files = (mmmod.MEMORY_FILE, mmmod.PREFS_FILE, mmmod.CONVERSATION_FILE)
        with tempfile.TemporaryDirectory() as tmpdir:
            mmmod.MEMORY_FILE = pathlib.Path(tmpdir) / "memories.json"
            mmmod.PREFS_FILE = pathlib.Path(tmpdir) / "prefs.json"
            mmmod.CONVERSATION_FILE = pathlib.Path(tmpdir) / "convs.json"
            try:
                mm.clear_all()
            finally:
                mmmod.MEMORY_FILE, mmmod.PREFS_FILE, mmmod.CONVERSATION_FILE = original_files

        assert len(mm.memories) == 0
        assert len(mm.conversation_history) == 0
        assert len(mm.preferences) == 0
