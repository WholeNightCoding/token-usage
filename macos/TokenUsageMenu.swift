// Token Usage — native menu-bar app for the dashboard.
//
// Lives in the macOS menu bar (NSStatusItem). On launch (and on every re-launch /
// dock-reopen) it starts the dashboard server if needed and opens the browser; the
// menu lets you re-open the panel, stop the background server, or quit (quitting
// also kills the server).
//
// Robustness: server liveness is decided by who is LISTENing on the port (lsof),
// python is resolved at RUNTIME from known locations (so a Homebrew python upgrade
// can't silently rot a baked-in path), and any startup failure raises a visible
// alert instead of opening a blank page. Zero runtime deps. Compiled with `swiftc`.

import Cocoa

// PYBIN is only a build-time *hint*; resolvePython() re-checks it and known
// fallbacks at runtime so an upgraded/removed interpreter can't break launch silently.
let PYBIN     = "@@PYBIN@@"
let SERVER_PY = "@@SERVER@@"
let LOG_FILE  = "@@LOG@@"
let URL_STR   = "@@URL@@"
let PORT      = "@@PORT@@"

func shq(_ s: String) -> String { "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'" }

final class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem!
    let statusLine = NSMenuItem(title: "服务：检查中…", action: nil, keyEquivalent: "")
    var busy = false

    func applicationDidFinishLaunching(_ note: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            let img = NSImage(systemSymbolName: "chart.bar.xaxis", accessibilityDescription: "Token Usage")
            img?.isTemplate = true
            button.image = img
            button.toolTip = "Token Usage Dashboard"
        }
        buildMenu()
        ensureServerAndOpen()               // same behavior as the old one-click launcher
        Timer.scheduledTimer(withTimeInterval: 3.0, repeats: true) { [weak self] _ in
            self?.refreshStatus()
        }
    }

    // Double-click the app while it's already running (or reopen it) → re-open the
    // dashboard instead of doing nothing. Fixes "I clicked again and nothing happened".
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        ensureServerAndOpen()
        return true
    }

    func buildMenu() {
        let menu = NSMenu()
        statusLine.isEnabled = false
        menu.addItem(statusLine)
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "打开面板", action: #selector(openPanel), keyEquivalent: "o"))
        menu.addItem(NSMenuItem(title: "停止服务", action: #selector(stopServer), keyEquivalent: "s"))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "退出", action: #selector(quitApp), keyEquivalent: "q"))
        for item in menu.items { item.target = self }
        statusItem.menu = menu
        refreshStatus()
    }

    // MARK: - process helpers

    @discardableResult
    func run(_ launch: String, _ args: [String], capture: Bool = false) -> Int32 {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: launch)
        p.arguments = args
        if capture { p.standardOutput = Pipe(); p.standardError = Pipe() }
        do { try p.run() } catch { return -1 }
        p.waitUntilExit()
        return p.terminationStatus
    }

    // Source of truth: who is LISTENing on our port. Robust — no name-substring
    // false positives (an unrelated process whose cmdline merely contains the
    // server path must not read as "running"), and gives exact PIDs to kill.
    func portPIDs() -> [Int32] {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/sbin/lsof")
        p.arguments = ["-nP", "-iTCP:\(PORT)", "-sTCP:LISTEN", "-t"]
        let pipe = Pipe(); p.standardOutput = pipe; p.standardError = Pipe()
        do { try p.run() } catch { return [] }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        let s = String(data: data, encoding: .utf8) ?? ""
        return s.split(whereSeparator: { $0 == "\n" || $0 == " " }).compactMap { Int32($0) }
    }

    func isRunning() -> Bool { !portPIDs().isEmpty }

    func killServer() {
        for pid in portPIDs() { kill(pid, SIGTERM) }
    }

    // Only used for the post-start readiness wait; hits "/" so it does log a line.
    func pingOK() -> Bool {
        run("/usr/bin/curl", ["-s", "-o", "/dev/null", "--max-time", "1", URL_STR], capture: true) == 0
    }

    // Resolve python at runtime: prefer the stable Homebrew symlink (follows the
    // current python3 across version bumps), then the build-time hint, then other
    // common locations. nil if none is executable — caller shows a visible alert.
    func resolvePython() -> String? {
        let candidates = [
            "/opt/homebrew/bin/python3",   // brew stable symlink (arm64)
            PYBIN,                          // exact interpreter resolved at build time
            "/usr/local/bin/python3",       // brew (intel)
            "/usr/bin/python3",             // system / Xcode CLT
        ]
        let fm = FileManager.default
        for c in candidates where fm.isExecutableFile(atPath: c) { return c }
        return nil
    }

    func logMarker(_ s: String) {
        let line = "\n# [app] \(s)\n"
        if let fh = FileHandle(forWritingAtPath: LOG_FILE) {
            fh.seekToEndOfFile(); fh.write(line.data(using: .utf8)!); fh.closeFile()
        } else {
            try? line.write(toFile: LOG_FILE, atomically: true, encoding: .utf8)
        }
    }

    func startServer(_ py: String) {
        if isRunning() { return }
        logMarker("start server via \(py)")
        let cmd = "/usr/bin/nohup \(shq(py)) \(shq(SERVER_PY)) --no-open >> \(shq(LOG_FILE)) 2>&1 < /dev/null &"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = ["-c", cmd]
        try? p.run()   // sh backgrounds the server and returns; do not wait on the server
        p.waitUntilExit()
    }

    @discardableResult
    func waitUntilUp(_ timeout: Double) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if pingOK() { return true }
            usleep(300_000)
        }
        return false
    }

    func openBrowser() {
        if let url = URL(string: URL_STR) { NSWorkspace.shared.open(url) }
    }

    func showAlert(_ text: String) {
        DispatchQueue.main.async {
            NSApp.activate(ignoringOtherApps: true)
            let a = NSAlert()
            a.messageText = "Token Usage"
            a.informativeText = text
            a.alertStyle = .warning
            a.runModal()
        }
    }

    // MARK: - actions (heavy work off the main thread so the menu stays snappy)

    func ensureServerAndOpen() {
        if busy { return }
        busy = true
        DispatchQueue.global().async { [weak self] in
            guard let self = self else { return }
            defer { DispatchQueue.main.async { self.busy = false; self.refreshStatus() } }
            if !self.isRunning() {
                guard let py = self.resolvePython() else {
                    self.showAlert("找不到 python3。请安装 Homebrew python 或 Xcode Command Line Tools 后重试。")
                    return
                }
                self.startServer(py)
                if !self.waitUntilUp(15) {
                    self.showAlert("仪表板服务启动失败。\n\n请查看日志：\n\(LOG_FILE)")
                    return   // never open a blank page on failure
                }
            }
            DispatchQueue.main.async { self.openBrowser() }
        }
    }

    @objc func openPanel() { ensureServerAndOpen() }

    @objc func stopServer() {
        DispatchQueue.global().async { [weak self] in
            self?.killServer()
            DispatchQueue.main.async { self?.refreshStatus() }
        }
    }

    @objc func quitApp() {
        killServer()            // synchronous: kill the server before we exit
        NSApp.terminate(nil)
    }

    func refreshStatus() {
        statusLine.title = isRunning() ? "● 服务运行中  :\(PORT)" : "○ 服务已停止"
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)   // menu-bar only, no Dock icon
let delegate = AppDelegate()
app.delegate = delegate
app.run()
