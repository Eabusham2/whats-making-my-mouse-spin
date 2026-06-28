/*
 * whats-making-my-mouse-spin - native GUI (Win32, C)
 * ==================================================
 *
 * A tiny always-on-top window that continuously shows whether your mouse
 * cursor is spinning and, if so, which process is responsible:
 *
 *   FULL SPIN     -> IDC_WAIT        (OCR_WAIT, res id 32514)  busy ring
 *   POINTER SPIN  -> IDC_APPSTARTING (OCR_APPSTARTING, 32650)  arrow + spinner
 *
 * It reports process name + PID + spin type + whether the window is hung.
 * No dependencies beyond the Win32 libraries that ship with Windows.
 *
 * Build (MSVC, from a "Developer Command Prompt"):
 *     cl /W4 /O2 mouse_spin_gui.c /link user32.lib gdi32.lib /SUBSYSTEM:WINDOWS
 *
 * Build (MinGW-w64):
 *     gcc mouse_spin_gui.c -o mouse_spin_gui.exe -mwindows -luser32 -lgdi32
 *
 * Then just run mouse_spin_gui.exe. Run it as Administrator if you want names
 * for elevated processes too.
 */

#define WIN32_LEAN_AND_MEAN
/* Target Vista+ so GetIconInfoExW / QueryFullProcessImageNameW / IsHungAppWindow
   / GetGUIThreadInfo and PROCESS_QUERY_LIMITED_INFORMATION are all declared. */
#ifndef WINVER
#define WINVER 0x0600
#endif
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0600
#endif
#include <windows.h>
#include <wchar.h>

#ifndef OCR_WAIT
#define OCR_WAIT 32514
#endif
#ifndef OCR_APPSTARTING
#define OCR_APPSTARTING 32650
#endif

#define ID_TIMER 1
#define POLL_MS  150

typedef enum { SPIN_NONE, SPIN_POINTER, SPIN_FULL, SPIN_HIDDEN } SpinKind;

typedef struct {
    SpinKind kind;
    DWORD    pid;
    BOOL     hung;
    int      resId;
    wchar_t  name[260];
    wchar_t  title[256];
    wchar_t  via[32];
} SpinState;

static SpinState g_state;
static HFONT g_fontBig;
static HFONT g_fontBody;

/* Identify a cursor by its OCR_* resource id (robust to custom cursor schemes).
   GetIconInfoExW creates bitmaps we must free, or we leak GDI objects. */
static int cursor_res_id(HCURSOR hc)
{
    ICONINFOEXW ii;
    ZeroMemory(&ii, sizeof(ii));
    ii.cbSize = sizeof(ii);
    if (!GetIconInfoExW(hc, &ii))
        return 0;
    if (ii.hbmMask)  DeleteObject(ii.hbmMask);
    if (ii.hbmColor) DeleteObject(ii.hbmColor);
    return (int)ii.wResID;
}

/* Resolve the window controlling the cursor into a process name/pid/hung flag. */
static void resolve_process(HWND hwnd, SpinState *s)
{
    HWND root = GetAncestor(hwnd, GA_ROOT);
    if (!root) root = hwnd;

    DWORD pid = 0;
    GetWindowThreadProcessId(root, &pid);
    s->pid  = pid;
    s->hung = IsHungAppWindow(root);
    GetWindowTextW(root, s->title, 256);

    s->name[0] = L'\0';
    HANDLE h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (h) {
        wchar_t path[1024];
        DWORD sz = (DWORD)(sizeof(path) / sizeof(path[0]));
        if (QueryFullProcessImageNameW(h, 0, path, &sz)) {
            wchar_t *base = wcsrchr(path, L'\\');
            lstrcpynW(s->name, base ? base + 1 : path, 260);
        }
        CloseHandle(h);
    }
    if (s->name[0] == L'\0')
        lstrcpynW(s->name, L"(name unavailable - run as admin?)", 260);
}

/* Sample the cursor and update the global state shown by WM_PAINT. */
static void update_state(void)
{
    SpinState s;
    ZeroMemory(&s, sizeof(s));

    CURSORINFO ci;
    ZeroMemory(&ci, sizeof(ci));
    ci.cbSize = sizeof(ci);
    if (!GetCursorInfo(&ci)) { g_state = s; return; }

    if (!(ci.flags & CURSOR_SHOWING) || !ci.hCursor) {
        s.kind = SPIN_HIDDEN;
        g_state = s;
        return;
    }

    s.resId = cursor_res_id(ci.hCursor);
    if (s.resId == OCR_WAIT)              s.kind = SPIN_FULL;
    else if (s.resId == OCR_APPSTARTING)  s.kind = SPIN_POINTER;
    else { s.kind = SPIN_NONE; g_state = s; return; }

    /* Who controls the cursor: capture window > under pointer > foreground. */
    HWND target = NULL;
    const wchar_t *via = L"";

    GUITHREADINFO gti;
    ZeroMemory(&gti, sizeof(gti));
    gti.cbSize = sizeof(gti);
    if (GetGUIThreadInfo(0, &gti) && gti.hwndCapture) {
        target = gti.hwndCapture;
        via = L"mouse-capture";
    }
    if (!target) { target = WindowFromPoint(ci.ptScreenPos); via = L"under-cursor"; }
    if (!target) { target = GetForegroundWindow();           via = L"foreground"; }

    if (target) {
        lstrcpynW(s.via, via, 32);
        resolve_process(target, &s);
    }
    g_state = s;
}

static void paint(HWND hwnd, HDC hdc)
{
    RECT rc;
    GetClientRect(hwnd, &rc);

    COLORREF bg;
    const wchar_t *headline;
    switch (g_state.kind) {
        case SPIN_FULL:    bg = RGB(170, 30, 30);  headline = L"FULL SPIN";     break;
        case SPIN_POINTER: bg = RGB(190, 115, 0);  headline = L"POINTER SPIN";  break;
        case SPIN_HIDDEN:  bg = RGB(70, 70, 70);   headline = L"CURSOR HIDDEN"; break;
        default:           bg = RGB(25, 110, 55);  headline = L"NO SPIN";       break;
    }

    HBRUSH br = CreateSolidBrush(bg);
    FillRect(hdc, &rc, br);
    DeleteObject(br);

    SetBkMode(hdc, TRANSPARENT);
    SetTextColor(hdc, RGB(255, 255, 255));

    RECT rh = rc; rh.left += 16; rh.top += 12; rh.right -= 16;
    SelectObject(hdc, g_fontBig);
    DrawTextW(hdc, headline, -1, &rh, DT_LEFT | DT_TOP | DT_SINGLELINE);

    wchar_t body[1024];
    if (g_state.kind == SPIN_FULL || g_state.kind == SPIN_POINTER) {
        wsprintfW(body,
                  L"Process:  %s  (PID %lu)\n"
                  L"Window:  \"%s\"\n"
                  L"Via:  %s%s",
                  g_state.name, g_state.pid,
                  g_state.title[0] ? g_state.title : L"(no title)",
                  g_state.via,
                  g_state.hung ? L"\nState:  NOT RESPONDING (hung)" : L"");
    } else if (g_state.kind == SPIN_HIDDEN) {
        lstrcpynW(body, L"Cursor is hidden/suppressed (full-screen app or game) - "
                        L"nothing to attribute.", 1024);
    } else {
        lstrcpynW(body, L"Your cursor is normal. Nothing is spinning right now.", 1024);
    }

    RECT rb = rc; rb.left += 16; rb.top += 58; rb.right -= 16; rb.bottom -= 12;
    SelectObject(hdc, g_fontBody);
    DrawTextW(hdc, body, -1, &rb, DT_LEFT | DT_TOP | DT_WORDBREAK | DT_NOPREFIX);
}

static LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
        case WM_CREATE:
            g_fontBig  = CreateFontW(30, 0, 0, 0, FW_BOLD, 0, 0, 0,
                                     DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
                                     CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                                     DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
            g_fontBody = CreateFontW(18, 0, 0, 0, FW_NORMAL, 0, 0, 0,
                                     DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
                                     CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                                     DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
            update_state();
            SetTimer(hwnd, ID_TIMER, POLL_MS, NULL);
            return 0;

        case WM_TIMER:
            update_state();
            InvalidateRect(hwnd, NULL, FALSE);
            return 0;

        case WM_PAINT: {
            PAINTSTRUCT ps;
            HDC hdc = BeginPaint(hwnd, &ps);
            paint(hwnd, hdc);
            EndPaint(hwnd, &ps);
            return 0;
        }

        case WM_ERASEBKGND:
            return 1;  /* we paint the whole client area ourselves */

        case WM_DESTROY:
            KillTimer(hwnd, ID_TIMER);
            if (g_fontBig)  DeleteObject(g_fontBig);
            if (g_fontBody) DeleteObject(g_fontBody);
            PostQuitMessage(0);
            return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

/* Use WinMain (not wWinMain) so MinGW links without -municode; we call the
   ...W APIs explicitly regardless, so the entry point's char width is moot. */
int WINAPI WinMain(HINSTANCE hInst, HINSTANCE hPrev, LPSTR cmd, int show)
{
    (void)hPrev; (void)cmd;

    const wchar_t *cls = L"MouseSpinGuiWindow";
    WNDCLASSW wc;
    ZeroMemory(&wc, sizeof(wc));
    wc.lpfnWndProc   = WndProc;
    wc.hInstance     = hInst;
    wc.hCursor       = LoadCursorW(NULL, IDC_ARROW);
    wc.lpszClassName = cls;
    RegisterClassW(&wc);

    HWND hwnd = CreateWindowExW(
        WS_EX_TOPMOST,
        cls, L"What's making my mouse spin?",
        WS_OVERLAPPEDWINDOW & ~WS_MAXIMIZEBOX & ~WS_THICKFRAME,
        CW_USEDEFAULT, CW_USEDEFAULT, 480, 220,
        NULL, NULL, hInst, NULL);
    if (!hwnd)
        return 1;

    ShowWindow(hwnd, show);
    UpdateWindow(hwnd);

    MSG m;
    while (GetMessageW(&m, NULL, 0, 0) > 0) {
        TranslateMessage(&m);
        DispatchMessageW(&m);
    }
    return 0;
}
