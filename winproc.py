"""
Énumération des processus Windows (chemin complet de l'exécutable), sans
dépendance externe : Toolhelp32 + QueryFullProcessImageNameW via ctypes.

Sert à deux choses :
  - retrouver le dossier d'installation d'Analys'immo à partir d'un `ADN.exe`
    en cours d'exécution, quel que soit l'endroit où il a été installé ;
  - repérer une ancienne Passerelle encore ouverte au lancement d'une nouvelle
    version, pour lui passer la main au lieu de bloquer l'utilisateur.

Toutes les fonctions sont tolérantes : en cas d'échec (hors Windows, droits
insuffisants…) elles renvoient une liste vide, jamais d'exception.
"""
import ctypes
import os
import sys
from ctypes import wintypes

_TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def _k32():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD)]
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    return k32


def _image_path(k32, pid: int) -> str:
    h = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        k32.CloseHandle(h)


def _session_id(k32, pid: int) -> int | None:
    sid = wintypes.DWORD()
    if k32.ProcessIdToSessionId(pid, ctypes.byref(sid)):
        return sid.value
    return None


def find_processes(names) -> list[dict]:
    """
    Processus dont le nom d'image figure dans `names` (insensible à la casse),
    ou satisfait `names` s'il s'agit d'une fonction `nom -> bool`.
    Retourne [{"pid", "name", "path", "session"}] ; `path` peut être vide si
    le processus appartient à un autre utilisateur.
    """
    if not sys.platform.startswith("win"):
        return []
    if callable(names):
        match = names
    else:
        wanted = {n.lower() for n in names}

        def match(n: str) -> bool:
            return n.lower() in wanted
    out: list[dict] = []
    try:
        k32 = _k32()
        snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snap or snap == _INVALID_HANDLE_VALUE:
            return []
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            ok = k32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                name = entry.szExeFile
                if match(name):
                    pid = int(entry.th32ProcessID)
                    out.append({"pid": pid, "name": name,
                                "path": _image_path(k32, pid),
                                "session": _session_id(k32, pid)})
                ok = k32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snap)
    except Exception:
        return []
    return out


def current_session() -> int | None:
    try:
        return _session_id(_k32(), os.getpid())
    except Exception:
        return None


def terminate(pid: int, wait_ms: int = 5000) -> bool:
    """Termine un processus et attend sa disparition. True si réussi."""
    if not sys.platform.startswith("win"):
        return False
    try:
        k32 = _k32()
        h = k32.OpenProcess(_PROCESS_TERMINATE | _SYNCHRONIZE, False, pid)
        if not h:
            return False
        try:
            if not k32.TerminateProcess(h, 0):
                return False
            k32.WaitForSingleObject(h, wait_ms)
            return True
        finally:
            k32.CloseHandle(h)
    except Exception:
        return False
