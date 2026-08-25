// Token Usage — native menu-bar app for the dashboard.
//
// Lives in the macOS menu bar (NSStatusItem). On launch it starts the dashboard
// server if needed and opens the browser; the menu lets you re-open the panel,
// stop the background server, or quit (quitting also kills the server).
//
// Zero runtime deps. Paths are injected at build time (build_app.sh) so no PATH
// assumption is baked in silently. Compiled with `swiftc`.

import Cocoa

let PYBIN    = "@@PYBIN@@"
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

    func startServer() {
        if isRunning() { return }
        let cmd = "/usr/bin/nohup \(shq(PYBIN)) \(shq(SERVER_PY)) --no-open >> \(shq(LOG_FILE)) 2>&1 < /dev/null &"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = ["-c", cmd]
        try? p.run()   // sh backgrounds the server and returns; do not wait on the server
        p.waitUntilExit()
    }

    func waitUntilUp(_ timeout: Double) {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if pingOK() { return }
            usleep(300_000)
        }
    }

    func openBrowser() {
        if let url = URL(string: URL_STR) { NSWorkspace.shared.open(url) }
    }

    // MARK: - actions (heavy work off the main thread so the menu stays snappy)

    func ensureServerAndOpen() {
        if busy { return }
        busy = true
        DispatchQueue.global().async { [weak self] in
            guard let self = self else { return }
            if !self.isRunning() {
                self.startServer()
                self.waitUntilUp(12)
            }
            DispatchQueue.main.async {
                self.openBrowser()
                self.busy = false
                self.refreshStatus()
            }
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
        statusLine.title = isRunning() ? "● 服务运行中  :8787" : "○ 服务已停止"
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)   // menu-bar only, no Dock icon
let delegate = AppDelegate()
app.delegate = delegate
app.run()
