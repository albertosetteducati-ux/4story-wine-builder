#!/usr/bin/env python3
"""Add faithful Toolhelp heap-list snapshot support to Wine 11.6.

This patch does not touch GameGuard or 4Story binaries.  It implements Windows
Toolhelp API behaviour that Wine 11.6 leaves as stubs:
  * TH32CS_SNAPHEAPLIST in CreateToolhelp32Snapshot
  * Heap32ListFirst
  * Heap32ListNext

The implementation reads the target process PEB and its ProcessHeaps array via
ReadProcessMemory.  It intentionally does not fake success or synthesize an
empty result.  Heap32First/Heap32Next remain untouched for this first iteration;
if a later trace reaches those stubs, implement them separately from evidence.
"""
from pathlib import Path
import re
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
toolhelp = root / "dlls/kernel32/toolhelp.c"
spec = root / "dlls/kernel32/kernel32.spec"

if not toolhelp.exists() or not spec.exists():
    raise SystemExit(f"Wine tree not found under {root}")

text = toolhelp.read_text()
orig = text

# 1) Snapshot bookkeeping for heap-list records.
old = """    int         module_count;\n    int         module_pos;\n    int         module_offset;\n    char        data[1];\n"""
new = """    int         module_count;\n    int         module_pos;\n    int         module_offset;\n    int         heap_count;\n    int         heap_pos;\n    int         heap_offset;\n    char        data[1];\n"""
if old not in text:
    raise SystemExit("anchor 1 not found: struct snapshot layout changed")
text = text.replace(old, new, 1)

# 2) Insert heap-list fetch/fill helpers immediately before fetch_process_thread.
anchor = "static BOOL fetch_process_thread( DWORD flags, SYSTEM_PROCESS_INFORMATION** pspi,\n"
if anchor not in text:
    raise SystemExit("anchor 2 not found: fetch_process_thread")
helpers = r'''/*
 * Read the target process heap list from its PEB.  Toolhelp heap snapshots
 * expose heap identifiers (addresses), not heap block contents, so this is
 * sufficient for TH32CS_SNAPHEAPLIST and mirrors Windows' observable contract.
 */
static BOOL fetch_heap_list( DWORD process, DWORD flags, HEAPLIST32 **heap_list, ULONG *num )
{
    PROCESS_BASIC_INFORMATION pbi;
    HANDLE hProcess;
    PVOID *heaps = NULL;
    PEB peb;
    ULONG i;
    BOOL ret = FALSE;

    *heap_list = NULL;
    *num = 0;
    if (!(flags & TH32CS_SNAPHEAPLIST)) return TRUE;

    if (process)
    {
        hProcess = OpenProcess( PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, FALSE, process );
        if (!hProcess) return FALSE;
    }
    else hProcess = GetCurrentProcess();

    if (!set_ntstatus( NtQueryInformationProcess( hProcess, ProcessBasicInformation,
                                                  &pbi, sizeof(pbi), NULL )))
        goto out;

    if (!ReadProcessMemory( hProcess, pbi.PebBaseAddress, &peb, sizeof(peb), NULL ))
        goto out;

    if (!peb.NumberOfHeaps)
    {
        ret = TRUE;
        goto out;
    }

    /* Refuse obviously corrupt counts before allocating from untrusted remote state. */
    if (peb.NumberOfHeaps > 0x10000)
    {
        SetLastError( ERROR_BAD_LENGTH );
        goto out;
    }

    heaps = HeapAlloc( GetProcessHeap(), 0, peb.NumberOfHeaps * sizeof(*heaps) );
    if (!heaps)
    {
        SetLastError( ERROR_NOT_ENOUGH_MEMORY );
        goto out;
    }

    if (!ReadProcessMemory( hProcess, peb.ProcessHeaps, heaps,
                            peb.NumberOfHeaps * sizeof(*heaps), NULL ))
        goto out;

    *heap_list = HeapAlloc( GetProcessHeap(), HEAP_ZERO_MEMORY,
                            peb.NumberOfHeaps * sizeof(**heap_list) );
    if (!*heap_list)
    {
        SetLastError( ERROR_NOT_ENOUGH_MEMORY );
        goto out;
    }

    for (i = 0; i < peb.NumberOfHeaps; ++i)
    {
        (*heap_list)[i].dwSize = sizeof(HEAPLIST32);
        (*heap_list)[i].th32ProcessID = process ? process : GetCurrentProcessId();
        (*heap_list)[i].th32HeapID = (ULONG_PTR)heaps[i];
        (*heap_list)[i].dwFlags = (heaps[i] == peb.ProcessHeap) ? HF32_DEFAULT : 0;
    }

    *num = peb.NumberOfHeaps;
    ret = TRUE;

out:
    if (!ret)
    {
        HeapFree( GetProcessHeap(), 0, *heap_list );
        *heap_list = NULL;
        *num = 0;
    }
    HeapFree( GetProcessHeap(), 0, heaps );
    if (process) CloseHandle( hProcess );
    return ret;
}

static void fill_heap_list( struct snapshot *snap, ULONG *offset, HEAPLIST32 *heap_list, ULONG num )
{
    snap->heap_count = num;
    snap->heap_pos = 0;
    if (!num) return;

    snap->heap_offset = *offset;
    memcpy( &snap->data[*offset], heap_list, num * sizeof(*heap_list) );
    *offset += num * sizeof(*heap_list);
}

'''
text = text.replace(anchor, helpers + anchor, 1)

# 3) Add heap state to CreateToolhelp32Snapshot locals.
old = """    SYSTEM_PROCESS_INFORMATION* spi = NULL;\n    LDR_DATA_TABLE_ENTRY *mod = NULL;\n    ULONG               num_pcs, num_thd, num_mod;\n"""
new = """    SYSTEM_PROCESS_INFORMATION* spi = NULL;\n    LDR_DATA_TABLE_ENTRY *mod = NULL;\n    HEAPLIST32          *heap_list = NULL;\n    ULONG               num_pcs = 0, num_thd = 0, num_mod = 0, num_heap = 0;\n"""
if old not in text:
    raise SystemExit("anchor 3 not found: snapshot locals")
text = text.replace(old, new, 1)

# 4) Accept heap-list-only snapshots.
old = "if (!(flags & (TH32CS_SNAPPROCESS|TH32CS_SNAPTHREAD|TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32)))"
new = "if (!(flags & (TH32CS_SNAPPROCESS|TH32CS_SNAPTHREAD|TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32|TH32CS_SNAPHEAPLIST)))"
if old not in text:
    raise SystemExit("anchor 4 not found: supported snapshot flags")
text = text.replace(old, new, 1)

# 5) Fetch heaps as part of snapshot creation.
old = """    if (fetch_module( process, flags, &mod, &num_mod ) &&\n        fetch_process_thread( flags, &spi, &num_pcs, &num_thd ))\n"""
new = """    if (fetch_module( process, flags, &mod, &num_mod ) &&\n        fetch_process_thread( flags, &spi, &num_pcs, &num_thd ) &&\n        fetch_heap_list( process, flags, &heap_list, &num_heap ))\n"""
if old not in text:
    raise SystemExit("anchor 5 not found: snapshot fetch chain")
text = text.replace(old, new, 1)

# 6) Allocate snapshot storage for heap records instead of logging a FIXME.
old = """        if (flags & TH32CS_SNAPPROCESS) sect_size += num_pcs * sizeof(PROCESSENTRY32W);\n        if (flags & TH32CS_SNAPTHREAD)  sect_size += num_thd * sizeof(THREADENTRY32);\n        if (flags & TH32CS_SNAPHEAPLIST)FIXME(\"Unimplemented: heap list snapshot\\n\");\n"""
new = """        if (flags & TH32CS_SNAPPROCESS) sect_size += num_pcs * sizeof(PROCESSENTRY32W);\n        if (flags & TH32CS_SNAPTHREAD)  sect_size += num_thd * sizeof(THREADENTRY32);\n        if (flags & TH32CS_SNAPHEAPLIST) sect_size += num_heap * sizeof(HEAPLIST32);\n"""
if old not in text:
    raise SystemExit("anchor 6 not found: heap FIXME")
text = text.replace(old, new, 1)

# 7) Fill heap records into the shared mapping.
old = """            fill_module( snap, &offset, process, mod, num_mod );\n            fill_process( snap, &offset, spi, num_pcs );\n            fill_thread( snap, &offset, spi, num_thd );\n"""
new = """            fill_module( snap, &offset, process, mod, num_mod );\n            fill_process( snap, &offset, spi, num_pcs );\n            fill_thread( snap, &offset, spi, num_thd );\n            fill_heap_list( snap, &offset, heap_list, num_heap );\n"""
if old not in text:
    raise SystemExit("anchor 7 not found: snapshot fill")
text = text.replace(old, new, 1)

# 8) Free temporary heap list.
old = """    HeapFree( GetProcessHeap(), 0, mod );\n    HeapFree( GetProcessHeap(), 0, spi );\n    if (!hSnapShot) return INVALID_HANDLE_VALUE;\n"""
new = """    HeapFree( GetProcessHeap(), 0, mod );\n    HeapFree( GetProcessHeap(), 0, spi );\n    HeapFree( GetProcessHeap(), 0, heap_list );\n    if (!hSnapShot) return INVALID_HANDLE_VALUE;\n"""
if old not in text:
    raise SystemExit("anchor 8 not found: snapshot cleanup")
text = text.replace(old, new, 1)

# 9) Replace Heap32ListFirst stub and add Heap32ListNext.
pattern = re.compile(r'''/\*+\n \*\s+Heap32ListFirst \(KERNEL32\.@\)\n \*\n \*/\nBOOL WINAPI Heap32ListFirst\(HANDLE hSnapshot, LPHEAPLIST32 lphl\)\n\{\n    FIXME\(\": stub\\n\"\);\n    return FALSE;\n\}\n''')
replacement = r'''/************************************************************************
 *              heap_list_next
 */
static BOOL heap_list_next( HANDLE hSnapshot, LPHEAPLIST32 lphl, BOOL first )
{
    struct snapshot *snap;
    BOOL ret = FALSE;

    if (lphl->dwSize < sizeof(HEAPLIST32))
    {
        SetLastError( ERROR_INSUFFICIENT_BUFFER );
        WARN("Result buffer too small (%lu)\n", lphl->dwSize );
        return FALSE;
    }

    if ((snap = MapViewOfFile( hSnapshot, FILE_MAP_ALL_ACCESS, 0, 0, 0 )))
    {
        if (first) snap->heap_pos = 0;
        if (snap->heap_pos < snap->heap_count)
        {
            LPHEAPLIST32 he = (HEAPLIST32 *)&snap->data[snap->heap_offset];
            *lphl = he[snap->heap_pos++];
            ret = TRUE;
        }
        else SetLastError( ERROR_NO_MORE_FILES );
        UnmapViewOfFile( snap );
    }
    return ret;
}

/************************************************************************
 *              Heap32ListFirst (KERNEL32.@)
 */
BOOL WINAPI Heap32ListFirst( HANDLE hSnapshot, LPHEAPLIST32 lphl )
{
    return heap_list_next( hSnapshot, lphl, TRUE );
}

/************************************************************************
 *              Heap32ListNext (KERNEL32.@)
 */
BOOL WINAPI Heap32ListNext( HANDLE hSnapshot, LPHEAPLIST32 lphl )
{
    return heap_list_next( hSnapshot, lphl, FALSE );
}
'''
text, count = pattern.subn(replacement, text, count=1)
if count != 1:
    raise SystemExit("anchor 9 not found: Heap32ListFirst stub")

if text == orig:
    raise SystemExit("no changes made")
toolhelp.write_text(text)

spec_text = spec.read_text()
old = "@ stub Heap32ListNext"
new = "@ stdcall Heap32ListNext(long ptr)"
if old not in spec_text:
    raise SystemExit("kernel32.spec Heap32ListNext stub not found")
spec.write_text(spec_text.replace(old, new, 1))

print("Applied 4Story Toolhelp heap-list compatibility implementation")
