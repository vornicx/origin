"""Tests para el sistema de autenticación JWT."""

import pytest


_test_counter = 0


def _unique_user():
    import time

    global _test_counter
    _test_counter += 1
    return f"tuser_{int(time.time())}_{_test_counter}"


def test_register_and_login():
    from core.auth import register_user, authenticate_user

    u = _unique_user()
    register_user(u, "TestPass_secure123!")
    result = authenticate_user(u, "TestPass_secure123!")
    assert "access_token" in result
    assert result["username"] == u
    assert result["role"] == "user"


def test_invalid_password():
    from core.auth import register_user, authenticate_user

    u = _unique_user()
    register_user(u, "TestPass_secure123!")
    with pytest.raises(Exception):
        authenticate_user(u, "wrongpass")


def test_duplicate_user():
    from core.auth import register_user

    u = _unique_user()
    register_user(u, "TestPass_secure123!")
    with pytest.raises(Exception):
        register_user(u, "AnotherSecure_pass456!")


def test_token_verify():
    from core.auth import register_user, authenticate_user, _verify_token

    u = _unique_user()
    register_user(u, "TestPass_secure123!")
    result = authenticate_user(u, "TestPass_secure123!")
    token = result["access_token"]
    username = _verify_token(token)
    assert username == u


def test_invalid_token():
    from core.auth import _verify_token

    username = _verify_token("invalid_token_here")
    assert username is None
