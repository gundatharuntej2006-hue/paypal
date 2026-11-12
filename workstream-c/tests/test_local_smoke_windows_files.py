"""Native Windows adversarial storage tests; empty files, no signing credentials."""

import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path

import pytest

from agentguard_workstream_c.integration.windows_files import NativeAPIError, NativeWindows, StorageSecurityError, WindowsOutput


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows handle security")
NAME = "local-smoke-mandates.json"


def set_test_acl(api, path, sddl):
    """Change only disposable test fixture permissions; never production paths."""
    descriptor, dacl = W.LPVOID(), W.LPVOID()
    present, defaulted = W.BOOL(), W.BOOL()
    get_dacl = api.a.GetSecurityDescriptorDacl
    get_dacl.argtypes = [W.LPVOID, C.POINTER(W.BOOL), C.POINTER(W.LPVOID), C.POINTER(W.BOOL)]
    get_dacl.restype = W.BOOL
    set_acl = api.a.SetNamedSecurityInfoW
    set_acl.argtypes = [W.LPWSTR, C.c_int, W.DWORD, W.LPVOID, W.LPVOID, W.LPVOID, W.LPVOID]
    set_acl.restype = W.DWORD
    api._check(api.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, C.byref(descriptor), None))
    try:
        api._check(get_dacl(descriptor, C.byref(present), C.byref(dacl), C.byref(defaulted)))
        api._check(set_acl(str(path), 1, 4 | 0x80000000, None, None, dacl, None) == 0)
    finally:
        api.k.LocalFree(descriptor)


def test_safe_custom_directory_handles_and_exact_acl(tmp_path):
    directory = tmp_path / "secure-output"
    with WindowsOutput(directory) as storage:
        stream = storage.create(NAME)
        stream.write(b"dummy noncredential data")
        stream.flush()
        storage.verify()
        assert len(storage.directories) == len(directory.parents) + 1
        assert storage.created_files == [NAME]
    assert (directory / NAME).read_bytes() == b"dummy noncredential data"
    with WindowsOutput(directory) as storage:
        with pytest.raises(StorageSecurityError):
            storage.create(NAME)
    assert (directory / NAME).read_bytes() == b"dummy noncredential data"


@pytest.mark.parametrize("right", [0x40, 0x10000, 0x40000, 0x80000, 0x40000000])
def test_unsafe_ancestor_mutation_rights_rejected(tmp_path, right):
    ancestor = tmp_path / "untrusted-ancestor"
    with WindowsOutput(ancestor):
        pass
    api = NativeWindows()
    rules = "".join(f"(A;OICI;FA;;;{sid})" for sid in sorted(api.trusted))
    set_test_acl(api, ancestor, "D:P" + rules + f"(A;;0x{right:x};;;WD)")
    with pytest.raises(StorageSecurityError):
        with WindowsOutput(ancestor / "output"):
            pytest.fail("Unsafe ancestor must not be accepted")
    assert not (ancestor / "output").exists()


def test_reparse_ancestor_rejected(tmp_path):
    # Read an existing Windows junction; creating junctions is restricted in some
    # Windows environments. Never modify it or write through its target.
    junction = Path(os.environ["SystemDrive"] + "/Users/All Users")
    assert junction.lstat().st_file_attributes & 0x400
    api = NativeWindows()
    handle = api.k.CreateFileW(str(junction), 0x20080, 3, None, 3, 0x02200000, None)
    api._check(handle != W.HANDLE(-1).value)
    try:
        with pytest.raises(StorageSecurityError):
            api.identity(handle, junction, True)
    finally:
        api.close(handle)
    with pytest.raises(StorageSecurityError):
        with WindowsOutput(junction / "output"):
            pytest.fail("Junction must be rejected")


def test_replacement_between_validation_and_creation_blocked(tmp_path):
    directory = tmp_path / "pinned-output"
    with WindowsOutput(directory) as storage:
        # Validation completed; attempt replacement before any file is created.
        for _, ancestor, _ in storage.directories[-2:]:
            with pytest.raises(OSError):
                os.rename(ancestor, ancestor.with_name(ancestor.name + "-replacement"))
        storage.create(NAME)
        storage.verify()


def test_file_handle_prevents_read_replace_and_delete(tmp_path):
    directory = tmp_path / "exclusive-output"
    with WindowsOutput(directory) as storage:
        storage.create(NAME)
        path = directory / NAME
        with pytest.raises(OSError):
            path.unlink()
        with pytest.raises(OSError):
            path.rename(directory / "renamed.json")
        with pytest.raises(OSError):
            path.read_bytes()
        storage.verify()
    assert path.read_bytes() == b""


def test_acl_verification_uses_actual_open_handle(tmp_path, monkeypatch):
    import msvcrt
    api = NativeWindows()
    checked = []
    original = api.verify_acl
    def verify(handle, **kwargs):
        checked.append(handle)
        return original(handle, **kwargs)
    monkeypatch.setattr(api, "verify_acl", verify)
    with WindowsOutput(tmp_path / "handle-check", _native=api) as storage:
        stream = storage.create(NAME)
        assert msvcrt.get_osfhandle(stream.fileno()) in checked
        storage.verify()
        assert checked.count(msvcrt.get_osfhandle(stream.fileno())) >= 2


def test_native_acl_query_failure_rejected(tmp_path, monkeypatch):
    api = NativeWindows()
    monkeypatch.setattr(api.a, "GetSecurityInfo", lambda *args: 5)
    with pytest.raises(StorageSecurityError):
        with WindowsOutput(tmp_path / "unverifiable", _native=api):
            pytest.fail("Native query failure must be fatal")
    assert not (tmp_path / "unverifiable").exists()


def test_file_handle_acl_query_failure_leaves_only_empty_created_file(tmp_path, monkeypatch):
    api = NativeWindows()
    with WindowsOutput(tmp_path / "file-query-failure", _native=api) as storage:
        monkeypatch.setattr(api.a, "GetSecurityInfo", lambda *args: 5)
        with pytest.raises(StorageSecurityError):
            storage.create(NAME)
        assert storage.created_files == [NAME]
        assert storage.files == []
    assert (storage.directory / NAME).read_bytes() == b""


@pytest.mark.parametrize("name", [NAME, "local-smoke-secrets.json"])
def test_native_existing_destination_is_never_overwritten(tmp_path, name):
    path = tmp_path / "existing-file"
    with WindowsOutput(path) as storage:
        stream = storage.create(name)
        stream.write(b"dummy preserved value")
    with WindowsOutput(path) as storage:
        with pytest.raises(StorageSecurityError):
            storage.create(name)
        assert storage.created_files == []
    assert (path / name).read_bytes() == b"dummy preserved value"


def test_identity_and_final_path_mismatch_rejected(tmp_path):
    with WindowsOutput(tmp_path / "identity") as storage:
        handle, path, identity = storage.directories[-1]
        with pytest.raises(StorageSecurityError):
            storage.native.identity(handle, path.with_name("other"), True)
        assert storage.native.identity(handle, path, True) == identity


def test_existing_permissive_output_rejected(tmp_path):
    directory = tmp_path / "permissive"
    with WindowsOutput(directory):
        pass
    api = NativeWindows()
    rules = "".join(f"(A;OICI;FA;;;{sid})" for sid in sorted(api.trusted))
    set_test_acl(api, directory, "D:P" + rules + "(A;;FR;;;WD)")
    with pytest.raises(StorageSecurityError):
        with WindowsOutput(directory):
            pytest.fail("Extra read trustee must be rejected")


@pytest.mark.parametrize("path", [r"\\localhost\share\secrets", r"\\?\C:\secrets", r"C:\secrets:stream"])
def test_remote_device_and_alternate_stream_paths_rejected(path):
    with pytest.raises(StorageSecurityError):
        with WindowsOutput(path):
            pytest.fail("Unsupported path must be rejected")


@pytest.mark.parametrize("api_name", [
    "CreateDirectoryW", "CloseHandle", "OpenProcessToken", "GetTokenInformation",
    "GetFileInformationByHandle", "GetFinalPathNameByHandleW", "GetSecurityDescriptorControl",
    "GetAce", "ConvertSidToStringSidW", "ConvertStringSecurityDescriptorToSecurityDescriptorW",
])
def test_native_boolean_failure_keeps_api_and_immediate_error(api_name):
    def failed(*args):
        C.set_last_error(5)
        return 0
    with pytest.raises(NativeAPIError) as caught:
        NativeWindows._call(api_name, failed, "TOKEN-AND-PATH-CANARY")
    assert caught.value.api_name == api_name
    assert caught.value.winerror == caught.value.captured_last_error == 5
    assert str(caught.value) == f"{api_name} failed (Win32 5): Access is denied."
    assert "CANARY" not in str(caught.value)


@pytest.mark.parametrize("operation", ["directory", "file"])
def test_creation_error_survives_cleanup_changing_last_error(monkeypatch, operation):
    api = NativeWindows()
    monkeypatch.setattr(api, "descriptor", lambda **kwargs: W.LPVOID())
    def failed(*args):
        C.set_last_error(32)
        return 0 if operation == "directory" else W.HANDLE(-1).value
    def freed(*args):
        C.set_last_error(87)  # Must not replace the original creation error.
        return None
    monkeypatch.setattr(api.k, "CreateDirectoryW" if operation == "directory" else "CreateFileW", failed)
    monkeypatch.setattr(api.k, "LocalFree", freed)
    with pytest.raises(NativeAPIError) as caught:
        (api.create_directory if operation == "directory" else api.create_file)(Path("PATH-SECRET-CANARY"))
    assert caught.value.winerror == caught.value.captured_last_error == 32
    assert caught.value.api_name == ("CreateDirectoryW" if operation == "directory" else "CreateFileW")
    assert "CANARY" not in str(caught.value)


def test_status_return_is_authoritative_not_stale_last_error(monkeypatch):
    api = NativeWindows()
    def failed(*args):
        C.set_last_error(87)
        return 5
    monkeypatch.setattr(api.a, "GetSecurityInfo", failed)
    with pytest.raises(NativeAPIError) as caught:
        api.verify_acl(None)
    assert caught.value.api_name == "GetSecurityInfo"
    assert caught.value.winerror == 5
    assert caught.value.captured_last_error == 87


@pytest.mark.parametrize("result", [None, 0])
def test_localfree_null_is_success_even_with_stale_error(result):
    def success(*args):
        C.set_last_error(5)
        return result
    assert NativeWindows._call("LocalFree", success, None, kind="localfree") is result


def test_localfree_nonnull_preserves_error():
    def failed(*args):
        C.set_last_error(6)
        return 123
    with pytest.raises(NativeAPIError) as caught:
        NativeWindows._call("LocalFree", failed, None, kind="localfree")
    assert caught.value.api_name == "LocalFree" and caught.value.winerror == 6


@pytest.mark.parametrize("code", [122, 183])
def test_expected_probe_and_existing_directory_status_remain_nonfatal(code):
    def expected(*args):
        C.set_last_error(code)
        return 0
    assert NativeWindows._call("GetTokenInformation" if code == 122 else "CreateDirectoryW",
                               expected, allowed_errors=(code,)) == 0


def test_unknown_error_description_and_api_are_sanitized():
    error = NativeAPIError("TOKEN-SECRET-CANARY", 123456)
    assert "CANARY" not in str(error)
    assert "123456" in str(error)
    assert error.description == "Windows reported a native API failure."


def test_close_failure_keeps_native_diagnostic_and_safe_original_cause():
    storage = WindowsOutput(Path("not-opened"))
    primary = NativeAPIError("CreateDirectoryW", 5)
    cleanup = NativeAPIError("CloseHandle", 6)
    class FailedClose:
        def close(self):
            raise cleanup
    storage.files = [(FailedClose(), None, None)]
    with pytest.raises(NativeAPIError) as caught:
        storage.__exit__(type(primary), primary, None)
    assert caught.value is cleanup
    assert caught.value.__cause__ is primary


def test_cleanup_native_failure_preserves_original_native_error(monkeypatch):
    api = NativeWindows()
    monkeypatch.setattr(api, "descriptor", lambda **kwargs: W.LPVOID())
    def create_failed(*args):
        C.set_last_error(5)
        return 0
    def free_failed(*args):
        C.set_last_error(6)
        return 123
    monkeypatch.setattr(api.k, "CreateDirectoryW", create_failed)
    monkeypatch.setattr(api.k, "LocalFree", free_failed)
    with pytest.raises(NativeAPIError) as caught:
        api.create_directory(Path("PATH-SECRET-CANARY"))
    assert caught.value.api_name == "LocalFree" and caught.value.winerror == 6
    assert caught.value.__cause__.api_name == "CreateDirectoryW"
    assert caught.value.__cause__.winerror == 5


def test_no_extended_error_does_not_reuse_callers_stale_error():
    C.set_last_error(32)
    with pytest.raises(NativeAPIError) as caught:
        NativeWindows._call("CloseHandle", lambda *args: 0, None)
    assert caught.value.winerror == caught.value.captured_last_error == 0
    assert "No extended Win32 error" in str(caught.value)


def test_multiple_cleanup_native_errors_are_retained():
    storage = WindowsOutput(Path("not-opened"))
    errors = [NativeAPIError("CloseHandle", 6), NativeAPIError("CloseHandle", 5)]
    class FailedClose:
        def __init__(self, error):
            self.error = error
        def close(self):
            raise self.error
    storage.files = [(FailedClose(error), None, None) for error in errors]
    with pytest.raises(NativeAPIError) as caught:
        storage.__exit__(None, None, None)
    assert {error.winerror for error in caught.value.cleanup_errors} == {5, 6}
