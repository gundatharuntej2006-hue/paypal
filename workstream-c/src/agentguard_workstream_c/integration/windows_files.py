"""Handle-based Windows storage for disposable credentials; no third-party APIs.

Local drive paths only. Ancestors stay open without FILE_SHARE_DELETE throughout
creation/writing/close. Administrators, SYSTEM, the caller, and (for ancestors
only) Windows TrustedInstaller are trusted. Unsupported ACLs fail closed.
"""

import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import sys


class StorageSecurityError(ValueError):
    """Sanitized native storage error."""


_API_NAMES = frozenset({
    "CreateFileW", "CreateDirectoryW", "CloseHandle", "GetCurrentProcess", "GetDriveTypeW", "LocalFree",
    "GetFileInformationByHandle", "GetFinalPathNameByHandleW", "OpenProcessToken", "GetTokenInformation",
    "ConvertSidToStringSidW", "ConvertStringSecurityDescriptorToSecurityDescriptorW", "GetSecurityInfo",
    "GetSecurityDescriptorControl", "GetAce",
})
_ERROR_DESCRIPTIONS = {
    0: "No extended Win32 error was reported.",
    2: "The system cannot find the file specified.",
    3: "The system cannot find the path specified.",
    5: "Access is denied.",
    6: "The handle is invalid.",
    8: "Insufficient memory resources.",
    32: "A sharing violation occurred.",
    80: "The file already exists.",
    87: "A parameter is incorrect.",
    122: "The supplied buffer is too small.",
    183: "The destination already exists.",
    1307: "The security identifier cannot be assigned as owner.",
    1336: "The access control list is invalid.",
    1337: "The security identifier is invalid.",
}


class NativeAPIError(StorageSecurityError):
    """Safe API/code diagnostics only; no arguments, paths or OS exception text.

    Descriptions use fixed text rather than formatting arbitrary OS/provider
    messages that could contain insertions. The returned status is authoritative
    for GetSecurityInfo; captured_last_error retains the separate immediate value.
    """

    def __init__(self, api_name, winerror, captured_last_error=0):
        self.api_name = api_name if api_name in _API_NAMES else "Windows native API"
        self.winerror = winerror if type(winerror) is int and 0 <= winerror <= 0xFFFFFFFF else 0
        self.captured_last_error = captured_last_error
        self.description = _ERROR_DESCRIPTIONS.get(self.winerror, "Windows reported a native API failure.")
        super().__init__(f"{self.api_name} failed (Win32 {self.winerror}): {self.description}")


class _SecurityAttributes(C.Structure):
    _fields_ = [("length", W.DWORD), ("descriptor", W.LPVOID), ("inherit", W.BOOL)]


class _FileInfo(C.Structure):
    _fields_ = [("attributes", W.DWORD), ("created", W.FILETIME), ("accessed", W.FILETIME),
                ("written", W.FILETIME), ("volume", W.DWORD), ("size_high", W.DWORD),
                ("size_low", W.DWORD), ("links", W.DWORD), ("index_high", W.DWORD), ("index_low", W.DWORD)]


class _ACL(C.Structure):
    _fields_ = [("revision", W.BYTE), ("reserved", W.BYTE), ("size", W.WORD),
                ("count", W.WORD), ("reserved2", W.WORD)]


class NativeWindows:
    """Small checked Win32 API surface. Handles, not names, supply security/identity."""

    def __init__(self):
        if os.name != "nt":
            raise StorageSecurityError("Windows handle security is required.")
        self.k = C.WinDLL("kernel32", use_last_error=True)
        self.a = C.WinDLL("advapi32", use_last_error=True)
        signatures = (
            (self.k, "CreateFileW", [W.LPCWSTR, W.DWORD, W.DWORD, C.POINTER(_SecurityAttributes), W.DWORD, W.DWORD, W.HANDLE], W.HANDLE),
            (self.k, "CreateDirectoryW", [W.LPCWSTR, C.POINTER(_SecurityAttributes)], W.BOOL),
            (self.k, "CloseHandle", [W.HANDLE], W.BOOL),
            (self.k, "GetCurrentProcess", [], W.HANDLE),
            (self.k, "GetDriveTypeW", [W.LPCWSTR], W.UINT),
            (self.k, "LocalFree", [W.LPVOID], W.LPVOID),
            (self.k, "GetFileInformationByHandle", [W.HANDLE, C.POINTER(_FileInfo)], W.BOOL),
            (self.k, "GetFinalPathNameByHandleW", [W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD], W.DWORD),
            (self.a, "OpenProcessToken", [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)], W.BOOL),
            (self.a, "GetTokenInformation", [W.HANDLE, C.c_int, W.LPVOID, W.DWORD, C.POINTER(W.DWORD)], W.BOOL),
            (self.a, "ConvertSidToStringSidW", [W.LPVOID, C.POINTER(W.LPWSTR)], W.BOOL),
            (self.a, "ConvertStringSecurityDescriptorToSecurityDescriptorW", [W.LPCWSTR, W.DWORD, C.POINTER(W.LPVOID), C.POINTER(W.DWORD)], W.BOOL),
            (self.a, "GetSecurityInfo", [W.HANDLE, C.c_int, W.DWORD, C.POINTER(W.LPVOID), C.POINTER(W.LPVOID), C.POINTER(W.LPVOID), C.POINTER(W.LPVOID), C.POINTER(W.LPVOID)], W.DWORD),
            (self.a, "GetSecurityDescriptorControl", [W.LPVOID, C.POINTER(W.WORD), C.POINTER(W.DWORD)], W.BOOL),
            (self.a, "GetAce", [W.LPVOID, W.DWORD, C.POINTER(W.LPVOID)], W.BOOL),
        )
        for dll, name, arguments, result in signatures:
            function = getattr(dll, name)
            function.argtypes, function.restype = arguments, result
        self.user = self._user_sid()
        self.trusted = {self.user, "S-1-5-18", "S-1-5-32-544"}

    @staticmethod
    def _check(ok):
        if not ok:
            raise StorageSecurityError("Native file-security verification failed.")

    @staticmethod
    def _call(api_name, function, *args, kind="bool", allowed_errors=()):
        C.set_last_error(0)  # Do not attribute a stale caller error to this API.
        result = function(*args)
        last_error = C.get_last_error()  # Immediately, before formatting or cleanup.
        if kind == "handle":
            failed = result == W.HANDLE(-1).value
        elif kind == "status":
            failed = result != 0
        elif kind == "localfree":
            failed = bool(result)  # LocalFree NULL (None/0) is SUCCESS.
        elif kind == "value":
            failed = False
        elif kind == "drive":
            failed = result in (0, 1)
        else:
            failed = not result
        code = int(result) if kind == "status" else last_error
        if failed and code not in allowed_errors:
            error = NativeAPIError(api_name, code, last_error)
            active = sys.exception()
            # Cleanup can fail while another native error is propagating. Keep
            # that safe cause, but never chain arbitrary path-bearing exceptions.
            raise error from active if isinstance(active, NativeAPIError) else None
        return result

    def close(self, handle):
        self._call("CloseHandle", self.k.CloseHandle, handle)

    def _free(self, pointer):
        self._call("LocalFree", self.k.LocalFree, pointer, kind="localfree")

    def _sid(self, pointer):
        text = W.LPWSTR()
        self._call("ConvertSidToStringSidW", self.a.ConvertSidToStringSidW, pointer, C.byref(text))
        try:
            return text.value
        finally:
            self._free(C.cast(text, W.LPVOID))

    def _user_sid(self):
        token = W.HANDLE()
        process = self._call("GetCurrentProcess", self.k.GetCurrentProcess, kind="value")
        self._call("OpenProcessToken", self.a.OpenProcessToken, process, 8, C.byref(token))
        try:
            needed = W.DWORD()
            self._call("GetTokenInformation", self.a.GetTokenInformation,
                       token, 1, None, 0, C.byref(needed), allowed_errors=(122,))
            self._check(needed.value > 0)
            buffer = C.create_string_buffer(needed.value)
            self._call("GetTokenInformation", self.a.GetTokenInformation, token, 1, buffer, needed, C.byref(needed))
            return self._sid(C.cast(buffer, C.POINTER(W.LPVOID))[0])
        finally:
            self.close(token)

    def descriptor(self, directory=False):
        flags = "OICI" if directory else ""
        sddl = "O:" + self.user + "D:P" + "".join(f"(A;{flags};FA;;;{sid})" for sid in sorted(self.trusted))
        descriptor = W.LPVOID()
        self._call("ConvertStringSecurityDescriptorToSecurityDescriptorW",
                   self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW, sddl, 1, C.byref(descriptor), None)
        return descriptor

    def open_directory(self, path):
        # Share read/write for normal filesystem use, but never share delete/rename.
        # FILE_LIST_DIRECTORY is essential: attribute-only handles do not impose
        # the same sharing constraints. Hold actual read access while pinning.
        handle = self._call("CreateFileW", self.k.CreateFileW,
                            str(path), 0x20081, 3, None, 3, 0x02200000, None, kind="handle")
        return handle

    def create_directory(self, path):
        descriptor = self.descriptor(directory=True)
        try:
            attributes = _SecurityAttributes(C.sizeof(_SecurityAttributes), descriptor, False)
            self._call("CreateDirectoryW", self.k.CreateDirectoryW,
                       str(path), C.byref(attributes), allowed_errors=(183,))
        finally:
            self._free(descriptor)

    def create_file(self, path):
        descriptor = self.descriptor()
        try:
            attributes = _SecurityAttributes(C.sizeof(_SecurityAttributes), descriptor, False)
            # CREATE_NEW, protected ACL at creation, no read/write/delete sharing.
            handle = self._call("CreateFileW", self.k.CreateFileW,
                                str(path), 0x80000000 | 0x40000000 | 0x20000, 0,
                                C.byref(attributes), 1, 0x00200080, None, kind="handle")
            return handle
        finally:
            self._free(descriptor)

    def identity(self, handle, expected_path, directory):
        info = _FileInfo()
        self._call("GetFileInformationByHandle", self.k.GetFileInformationByHandle, handle, C.byref(info))
        self._check(not info.attributes & 0x400)  # Reject opened reparse point itself.
        self._check(bool(info.attributes & 0x10) == directory)
        if not directory:
            self._check(info.links == 1)
        buffer = C.create_unicode_buffer(32768)
        length = self._call("GetFinalPathNameByHandleW", self.k.GetFinalPathNameByHandleW, handle, buffer, len(buffer), 0)
        self._check(0 < length < len(buffer))
        actual = buffer.value
        if actual.startswith("\\\\?\\"):
            actual = actual[4:]
        self._check(os.path.normcase(os.path.normpath(actual)) == os.path.normcase(str(expected_path)))
        return info.volume, info.index_high, info.index_low

    def verify_acl(self, handle, *, strict=False, directory=False, volume_root=False):
        owner, dacl, descriptor = W.LPVOID(), W.LPVOID(), W.LPVOID()
        self._call("GetSecurityInfo", self.a.GetSecurityInfo,
                   handle, 1, 5, C.byref(owner), None, C.byref(dacl), None, C.byref(descriptor), kind="status")
        try:
            self._check(bool(owner) and bool(dacl))  # A null DACL grants everyone access.
            owner_sid = self._sid(owner)
            installer = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
            self._check(owner_sid in (self.trusted if strict else self.trusted | {installer}))
            control, revision = W.WORD(), W.DWORD()
            self._call("GetSecurityDescriptorControl", self.a.GetSecurityDescriptorControl,
                       descriptor, C.byref(control), C.byref(revision))
            if strict:
                self._check(owner_sid == self.user and control.value & 0x1000)
            seen = set()
            for index in range(C.cast(dacl, C.POINTER(_ACL)).contents.count):
                ace = W.LPVOID()
                self._call("GetAce", self.a.GetAce, dacl, index, C.byref(ace))
                kind = C.c_ubyte.from_address(ace.value).value
                flags = C.c_ubyte.from_address(ace.value + 1).value
                size = C.c_ushort.from_address(ace.value + 2).value
                self._check(kind in (0, 1) and size >= 12)  # Unknown/object/callback ACEs are unsupported.
                mask = C.c_uint32.from_address(ace.value + 4).value
                sid = self._sid(ace.value + 8)
                if strict:
                    self._check(kind == 0 and sid in self.trusted and sid not in seen)
                    self._check(mask == 0x1F01FF and flags == (3 if directory else 0))
                    seen.add(sid)
                elif kind == 0 and not flags & 8:  # INHERIT_ONLY does not apply to this ancestor.
                    # OWNER RIGHTS refers to the already-verified owner, not a
                    # separate broad trustee (pytest uses this SID for temp ACLs).
                    if sid == "S-1-3-4":
                        sid = owner_sid
                    # DELETE, DELETE_CHILD, WRITE_DAC, WRITE_OWNER, GENERIC_WRITE/ALL.
                    # Child creation alone on existing root ancestors is safe while pinned.
                    destructive = 0x10000 | 0x40 | 0x40000 | 0x80000 | 0x40000000 | 0x10000000 | 0x2 | 0x4 | 0x10 | 0x100
                    # Standard Windows volume roots grant Authenticated Users
                    # only ADD_SUBDIRECTORY. Existing components are pinned and
                    # CREATE_NEW/strict ACL verification protect the final one.
                    if volume_root and mask == 0x4:
                        continue
                    self._check(sid in self.trusted | {installer} or not mask & destructive)
            if strict:
                self._check(seen == self.trusted)
        finally:
            self._free(descriptor)


class WindowsOutput:
    """Pin every component and exclusively create verified handle-backed streams.

    No cleanup deletes names. On failure, created_files identifies only this
    invocation's successful CREATE_NEW operations for manual review.
    """

    def __init__(self, directory, *, _native=None):
        self.directory = Path(os.path.abspath(directory))
        self.native = _native
        self.directories = []
        self.files = []
        self.created_files = []

    def __enter__(self):
        try:
            api = self.native = self.native or NativeWindows()
            path = self.directory
            if not path.drive or str(path).startswith("\\\\") or ":" in str(path)[2:]:
                raise StorageSecurityError("Only ordinary local drive paths are supported.")
            api._check(api._call("GetDriveTypeW", api.k.GetDriveTypeW, path.anchor, kind="drive") == 3)
            components = list(reversed(path.parents)) + [path]
            for component in components:
                if component == path:
                    api.create_directory(component)
                handle = api.open_directory(component)
                self.directories.append((handle, component, api.identity(handle, component, True)))
                api.verify_acl(handle, strict=component == path, directory=True,
                               volume_root=component == Path(path.anchor))
            return self
        except Exception as error:
            self.__exit__(type(error), error, error.__traceback__)
            raise

    def create(self, name):
        import msvcrt
        if name not in ("local-smoke-mandates.json", "local-smoke-secrets.json"):
            raise StorageSecurityError("Unsupported credential filename.")
        path = self.directory / name
        handle = self.native.create_file(path)
        self.created_files.append(name)
        try:
            identity = self.native.identity(handle, path, False)
            self.native.verify_acl(handle, strict=True)
            descriptor = msvcrt.open_osfhandle(handle, os.O_BINARY | os.O_RDWR)
        except Exception:
            self.native.close(handle)
            raise
        try:
            stream = os.fdopen(descriptor, "wb")
        except Exception:
            os.close(descriptor)
            raise
        self.files.append((stream, path, identity))
        return stream

    def verify(self):
        import msvcrt
        for handle, path, identity in self.directories:
            self.native._check(self.native.identity(handle, path, True) == identity)
            self.native.verify_acl(handle, strict=path == self.directory, directory=True,
                                   volume_root=path == Path(self.directory.anchor))
        for stream, path, identity in self.files:
            handle = msvcrt.get_osfhandle(stream.fileno())
            self.native._check(self.native.identity(handle, path, False) == identity)
            self.native.verify_acl(handle, strict=True)

    def __exit__(self, *args):
        failures = []
        for stream, _, _ in reversed(self.files):
            try:
                stream.close()
            except Exception as error:
                failures.append(error)
        self.files.clear()
        for handle, _, _ in reversed(self.directories):
            try:
                self.native.close(handle)
            except Exception as error:
                failures.append(error)
        self.directories.clear()
        if failures:
            native = next((error for error in failures if isinstance(error, NativeAPIError)), None)
            error = native or StorageSecurityError("Output close failed; review partial output.")
            error.cleanup_errors = tuple(item for item in failures if isinstance(item, NativeAPIError))
            cause = args[1] if len(args) > 1 and isinstance(args[1], NativeAPIError) else None
            raise error from cause
