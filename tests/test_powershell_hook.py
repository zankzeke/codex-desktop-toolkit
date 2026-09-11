import pytest

from powershell_hook import _validate_proxy_address, get_hook_script


def test_proxy_address_validation_blocks_injection():
    _validate_proxy_address("127.0.0.1:10808")
    _validate_proxy_address("[::1]:10808")
    with pytest.raises(ValueError):
        _validate_proxy_address("127.0.0.1:10808'; Write-Host HACK; #")
    with pytest.raises(ValueError):
        _validate_proxy_address("127.0.0.1:70000")


def test_generated_hook_restores_environment():
    script = get_hook_script("127.0.0.1:10808")
    assert "try {" in script
    assert "finally {" in script
    assert "Remove-Item Env:HTTP_PROXY" in script
    assert "Remove-Item Env:NO_PROXY" in script
