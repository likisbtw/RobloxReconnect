import ctypes
from ctypes import wintypes
import sys
import os
import time
import threading

# Global for background mutex killer
_mutex_killer_running = False
_mutex_killer_thread = None

# Constants
SystemExtendedHandleInformation = 64  # Class 64 for x64
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
CNST_PID_ACCESS = 0x1F0FFF
DUPLICATE_CLOSE_SOURCE = 0x00000001
DUPLICATE_SAME_ACCESS = 0x00000002

# Libraries
ntdll = ctypes.WinDLL("ntdll.dll")
kernel32 = ctypes.WinDLL("kernel32.dll")

# Set up function signatures for proper 64-bit handle handling
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

kernel32.GetCurrentProcess.argtypes = []
kernel32.GetCurrentProcess.restype = wintypes.HANDLE

kernel32.DuplicateHandle.argtypes = [
    wintypes.HANDLE,  # hSourceProcessHandle
    wintypes.HANDLE,  # hSourceHandle
    wintypes.HANDLE,  # hTargetProcessHandle
    ctypes.POINTER(wintypes.HANDLE),  # lpTargetHandle
    wintypes.DWORD,   # dwDesiredAccess
    wintypes.BOOL,    # bInheritHandle
    wintypes.DWORD    # dwOptions
]
kernel32.DuplicateHandle.restype = wintypes.BOOL

kernel32.GetLastError.argtypes = []
kernel32.GetLastError.restype = wintypes.DWORD

# Structures
class SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX(ctypes.Structure):
    _fields_ = [
        ("Object", ctypes.c_ulonglong),          # PVOID
        ("UniqueProcessId", ctypes.c_ulonglong), # ULONG_PTR
        ("HandleValue", ctypes.c_ulonglong),     # ULONG_PTR
        ("GrantedAccess", wintypes.ULONG),
        ("CreatorBackTraceIndex", wintypes.USHORT),
        ("ObjectTypeIndex", wintypes.USHORT),
        ("HandleAttributes", wintypes.ULONG),
        ("Reserved", wintypes.ULONG),
    ]

class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", ctypes.c_wchar_p)
    ]

class OBJECT_NAME_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Name", UNICODE_STRING)
    ]

PROCESS_DUP_HANDLE = 0x0040

def get_process_id_by_name(process_name):
    import psutil
    pids = []
    for proc in psutil.process_iter(['pid', 'name']):
        if proc.info['name'] == process_name:
            pids.append(proc.info['pid'])
    return pids

def close_roblox_mutex():
    found_any = False
    
    pids = get_process_id_by_name("RobloxPlayerBeta.exe")
    if not pids:
        return False
    
    target_pids = set(pids)

    # 1. Query System Handle Info (Extended)
    size = 0x10000
    while True:
        buf = ctypes.create_string_buffer(size)
        return_length = wintypes.ULONG()
        status = ntdll.NtQuerySystemInformation(
            SystemExtendedHandleInformation,
            buf,
            size,
            ctypes.byref(return_length)
        )
        
        if (status & 0xFFFFFFFF) == STATUS_INFO_LENGTH_MISMATCH:
            size = return_length.value + 0x2000
            continue
        elif status < 0:
            print(f"NtQuerySystemInformation failed checking handles: {hex(status & 0xFFFFFFFF)}")
            return False
        else:
            break
            
    # 2. Parse Buffer
    current_ptr = ctypes.addressof(buf)
    count = ctypes.c_ulonglong.from_address(current_ptr).value # read 8 bytes
    current_ptr += 16 # Skip NumHandles + Reserved
    
    struct_size = ctypes.sizeof(SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX)
    for i in range(count):
        entry = SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX.from_address(current_ptr)
        current_ptr += struct_size
        
        current_pid = int(entry.UniqueProcessId)
        
        if current_pid in target_pids:
            # Found a handle for our process.
            h_proc = kernel32.OpenProcess(PROCESS_DUP_HANDLE, False, int(entry.UniqueProcessId))
            if not h_proc:
                err = kernel32.GetLastError()
                continue
            
            dup_handle = wintypes.HANDLE()
            success = kernel32.DuplicateHandle(
                h_proc,
                wintypes.HANDLE(int(entry.HandleValue)),
                kernel32.GetCurrentProcess(),
                ctypes.byref(dup_handle),
                0,
                False,
                DUPLICATE_SAME_ACCESS
            )
            
            kernel32.CloseHandle(h_proc)
            
            if not success:
               err = kernel32.GetLastError()
               if handles_processed < 3:
                   print(f"    DuplicateHandle failed: Error {err}")
               continue
               
            
            # Query Name
            name_info_size = 0x400
            name_buf = ctypes.create_string_buffer(name_info_size)
            ret_len = wintypes.ULONG()
            
            st = ntdll.NtQueryObject(
                dup_handle,
                1, # ObjectNameInformation
                name_buf,
                name_info_size,
                ctypes.byref(ret_len)
            )
            
            if st >= 0:
                name_info = OBJECT_NAME_INFORMATION.from_address(ctypes.addressof(name_buf))
                if name_info.Name.Buffer:
                    try:
                        name_str = name_info.Name.Buffer
                        # Close ANY handle with ROBLOX in the name
                        if "ROBLOX" in name_str:
                            print(f"FOUND ROBLOX HANDLE: {name_str} in PID {entry.UniqueProcessId}")
                            
                            h_proc_kill = kernel32.OpenProcess(PROCESS_DUP_HANDLE, False, int(entry.UniqueProcessId))
                            if h_proc_kill:
                                # Use DUPLICATE_CLOSE_SOURCE to close the handle in the source process
                                # Don't need a copy in our process, so pass None for target handle
                                kill_success = kernel32.DuplicateHandle(
                                    h_proc_kill,
                                    wintypes.HANDLE(int(entry.HandleValue)),
                                    None,  # No target process
                                    None,  # No target handle needed
                                    0,
                                    False,
                                    DUPLICATE_CLOSE_SOURCE
                                )
                                kernel32.CloseHandle(h_proc_kill)
                                
                                if kill_success:
                                    print("Mutex successfully closed!")
                                    found_any = True
                                else:
                                    print(f"Failed to close mutex: Error {kernel32.GetLastError()}")
                            else:
                                print(f"Failed to open process for killing: Error {kernel32.GetLastError()}")
                    except:
                        pass
            
            kernel32.CloseHandle(dup_handle)
    
    return found_any

def _mutex_killer_loop():
    """Background thread that continuously closes Roblox mutexes."""
    global _mutex_killer_running
    scan_count = 0
    while _mutex_killer_running:
        try:
            result = close_roblox_mutex_silent()
            scan_count += 1
            if result:
                print(f"[Mutex Killer] Closed mutex (scan #{scan_count})")
        except Exception as e:
            print(f"[Mutex Killer] Error: {e}")
        time.sleep(0.1)  # Check every 100ms for faster response

def close_roblox_mutex_silent():
    """Silent version of close_roblox_mutex for background use."""
    pids = get_process_id_by_name("RobloxPlayerBeta.exe")
    if not pids:
        return False
    
    target_pids = set(pids)
    found_any = False
    
    size = 0x10000
    while True:
        buf = ctypes.create_string_buffer(size)
        return_length = wintypes.ULONG()
        status = ntdll.NtQuerySystemInformation(
            SystemExtendedHandleInformation,
            buf,
            size,
            ctypes.byref(return_length)
        )
        
        if (status & 0xFFFFFFFF) == STATUS_INFO_LENGTH_MISMATCH:
            size = return_length.value + 0x2000
            continue
        elif status < 0:
            return False
        else:
            break
    
    current_ptr = ctypes.addressof(buf)
    count = ctypes.c_ulonglong.from_address(current_ptr).value
    current_ptr += 16
    struct_size = ctypes.sizeof(SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX)
    
    for i in range(count):
        entry = SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX.from_address(current_ptr)
        current_ptr += struct_size
        
        current_pid = int(entry.UniqueProcessId)
        if current_pid not in target_pids:
            continue
            
        h_proc = kernel32.OpenProcess(PROCESS_DUP_HANDLE, False, current_pid)
        if not h_proc:
            continue
        
        dup_handle = wintypes.HANDLE()
        success = kernel32.DuplicateHandle(
            h_proc,
            wintypes.HANDLE(int(entry.HandleValue)),
            kernel32.GetCurrentProcess(),
            ctypes.byref(dup_handle),
            0,
            False,
            DUPLICATE_SAME_ACCESS
        )
        kernel32.CloseHandle(h_proc)
        
        if not success:
            continue
        
        name_info_size = 0x400
        name_buf = ctypes.create_string_buffer(name_info_size)
        ret_len = wintypes.ULONG()
        
        st = ntdll.NtQueryObject(dup_handle, 1, name_buf, name_info_size, ctypes.byref(ret_len))
        
        if st >= 0:
            name_info = OBJECT_NAME_INFORMATION.from_address(ctypes.addressof(name_buf))
            if name_info.Name.Buffer:
                try:
                    name_str = name_info.Name.Buffer
                    # Close ANY handle with ROBLOX in the name (mutex, event, semaphore, etc.)
                    if "ROBLOX" in name_str:
                        h_proc_kill = kernel32.OpenProcess(PROCESS_DUP_HANDLE, False, current_pid)
                        if h_proc_kill:
                            kernel32.DuplicateHandle(
                                h_proc_kill,
                                wintypes.HANDLE(int(entry.HandleValue)),
                                None, None, 0, False,
                                DUPLICATE_CLOSE_SOURCE
                            )
                            kernel32.CloseHandle(h_proc_kill)
                            found_any = True
                except:
                    pass
        
        kernel32.CloseHandle(dup_handle)
    
    return found_any

def start_mutex_killer():
    """Start background thread to continuously close Roblox mutexes."""
    global _mutex_killer_running, _mutex_killer_thread
    if _mutex_killer_thread is not None and _mutex_killer_thread.is_alive():
        return  # Already running
    
    _mutex_killer_running = True
    _mutex_killer_thread = threading.Thread(target=_mutex_killer_loop, daemon=True)
    _mutex_killer_thread.start()
    print("[Mutex Killer] Background thread started")

def stop_mutex_killer():
    """Stop the background mutex killer thread."""
    global _mutex_killer_running, _mutex_killer_thread
    _mutex_killer_running = False
    if _mutex_killer_thread is not None:
        _mutex_killer_thread.join(timeout=2)
        _mutex_killer_thread = None
    print("[Mutex Killer] Background thread stopped")

if __name__ == "__main__":
    close_roblox_mutex()

