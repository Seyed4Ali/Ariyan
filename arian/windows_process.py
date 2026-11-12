"""Read-only Windows process probe. Failure never authorizes launching a terminal."""
import ctypes
import os
from ctypes import wintypes


def terminal_is_running(path):
    if os.name != 'nt':
        return False
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    class Entry(ctypes.Structure):
        _fields_ = [('dwSize',wintypes.DWORD),('cntUsage',wintypes.DWORD),
            ('th32ProcessID',wintypes.DWORD),('th32DefaultHeapID',ctypes.c_size_t),
            ('th32ModuleID',wintypes.DWORD),('cntThreads',wintypes.DWORD),
            ('th32ParentProcessID',wintypes.DWORD),('pcPriClassBase',wintypes.LONG),
            ('dwFlags',wintypes.DWORD),('szExeFile',wintypes.WCHAR*260)]
    kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    kernel.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes=[wintypes.HANDLE,ctypes.POINTER(Entry)]
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    expected=os.path.normcase(os.path.abspath(path))
    snapshot=kernel.CreateToolhelp32Snapshot(2,0)
    if snapshot == ctypes.c_void_p(-1).value:
        return False
    try:
        entry=Entry();entry.dwSize=ctypes.sizeof(entry)
        found=kernel.Process32FirstW(snapshot,ctypes.byref(entry))
        while found:
            if entry.szExeFile.lower()==os.path.basename(path).lower():
                handle=kernel.OpenProcess(0x1000,False,entry.th32ProcessID)
                if handle:
                    try:
                        size=wintypes.DWORD(32768);buffer=ctypes.create_unicode_buffer(size.value)
                        if kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(size)):
                            if os.path.normcase(os.path.abspath(buffer.value))==expected:
                                return True
                    finally:
                        kernel.CloseHandle(handle)
            found=kernel.Process32NextW(snapshot,ctypes.byref(entry))
        return False
    finally:
        kernel.CloseHandle(snapshot)
