// dewiar-audio-tap — звук приложений macOS для записи встреч Dewiar без BlackHole.
//
// Core Audio Process Tap (macOS 14.2+): приложение звонка продолжает играть в свои
// наушники/колонки как обычно, а копия его звука идёт сюда. Не нужен виртуальный драйвер
// и Multi-Output Device, громкость и смена наушников работают штатно.
//
// Режимы:
//   dewiar-audio-tap --list
//       JSON-массив приложений, подключённых к Core Audio:
//       [{"bundle":"us.zoom.xos","name":"zoom.us","playing":true,"pids":[123]}]
//       Вспомогательные процессы (Chrome Helper, Teams ModuleHost, WebKit.GPU) относятся
//       к приложению, в чьём .app лежит их исполняемый файл.
//   dewiar-audio-tap --capture all [--exclude-pid <pid>]...
//       Весь звук системы, кроме самого Dewiar (его голос/сигналы не должны попасть в запись).
//   dewiar-audio-tap --capture <bundle-id>[,<bundle-id>…]
//       Только выбранные приложения (например, us.zoom.xos,ru.yandex.telemost).
//
// stdout — s16le, 16 кГц, моно, строго по часам: если приложение молчит или ещё не играет,
//   пишутся нули. Число отсчётов всегда равно прошедшему времени — это часы встречи.
// stderr — JSON-строки состояния: {"ev":"status"|"tap"|"warning"|"error", ...}.
// stdin  — не используется; остановка — SIGTERM/SIGINT или закрытие stdout читателем.
//
// Разрешение: «Запись системного звука» (TCC kTCCServiceAudioCapture). Его спрашивают у
// ответственного (responsible) приложения. Внутри Dewiar.app это сам Dewiar — в его Info.plist
// должен быть NSAudioCaptureUsageDescription. При запуске из исходников macOS может
// отнести разрешение к Terminal/хосту Python; для пользовательской версии проверяйте
// подписанный .app. Частные API смены ответственного процесса не используются.
//
// Подход (tap на набор процессов + приватный aggregate device + пересборка при смене
// процессов/устройства + выдача по часам) повторяет идеи проекта raliev/audiotranscripts;
// реализация написана заново под протокол Dewiar.

import AppKit
import AVFoundation
import CoreAudio
import Foundation

let outRate = 16000.0

// MARK: - stderr JSON

let errLock = NSLock()
func report(_ ev: String, _ fields: [String: Any] = [:]) {
    var obj = fields
    obj["ev"] = ev
    guard let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]) else { return }
    errLock.lock(); defer { errLock.unlock() }
    FileHandle.standardError.write(data)
    FileHandle.standardError.write(Data([0x0A]))
}

// MARK: - Удержание dispatch sources

var keepAlive: [AnyObject] = []

// MARK: - Свойства Core Audio

func propAddr(_ sel: AudioObjectPropertySelector) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: sel, mScope: kAudioObjectPropertyScopeGlobal,
                               mElement: kAudioObjectPropertyElementMain)
}

func readProp<T>(_ obj: AudioObjectID, _ sel: AudioObjectPropertySelector, _ initial: T) -> T? {
    var a = propAddr(sel)
    var size = UInt32(MemoryLayout<T>.size)
    var value = initial
    let st = withUnsafeMutablePointer(to: &value) { AudioObjectGetPropertyData(obj, &a, 0, nil, &size, $0) }
    return st == noErr ? value : nil
}

func readString(_ obj: AudioObjectID, _ sel: AudioObjectPropertySelector) -> String? {
    var a = propAddr(sel)
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    var s: Unmanaged<CFString>?
    guard AudioObjectGetPropertyData(obj, &a, 0, nil, &size, &s) == noErr, let s else { return nil }
    return s.takeRetainedValue() as String
}

func readIDs(_ obj: AudioObjectID, _ sel: AudioObjectPropertySelector) -> [AudioObjectID] {
    var a = propAddr(sel)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(obj, &a, 0, nil, &size) == noErr, size > 0 else { return [] }
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(obj, &a, 0, nil, &size, &ids) == noErr else { return [] }
    return ids
}

let systemObj = AudioObjectID(kAudioObjectSystemObject)

func defaultOutput() -> AudioObjectID {
    readProp(systemObj, kAudioHardwarePropertyDefaultOutputDevice, AudioObjectID(0)) ?? 0
}

/// pid → объект процесса Core Audio (kAudioObjectUnknown, если процесс к звуку не подключён).
func processObject(for pid: pid_t) -> AudioObjectID {
    var a = propAddr(kAudioHardwarePropertyTranslatePIDToProcessObject)
    var qualifier = pid
    var obj = AudioObjectID(kAudioObjectUnknown)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    let st = AudioObjectGetPropertyData(systemObj, &a, UInt32(MemoryLayout<pid_t>.size), &qualifier, &size, &obj)
    return st == noErr ? obj : AudioObjectID(kAudioObjectUnknown)
}

// MARK: - Процессы и приложения

struct AudioProcess {
    let object: AudioObjectID
    let pid: pid_t
    let bundle: String
    let playing: Bool
}

func audioProcesses() -> [AudioProcess] {
    readIDs(systemObj, kAudioHardwarePropertyProcessObjectList).map { id in
        AudioProcess(object: id,
                     pid: readProp(id, kAudioProcessPropertyPID, pid_t(0)) ?? 0,
                     bundle: readString(id, kAudioProcessPropertyBundleID) ?? "",
                     playing: (readProp(id, kAudioProcessPropertyIsRunningOutput, UInt32(0)) ?? 0) != 0)
    }
}

/// Самое внешнее .app, внутри которого лежит исполняемый файл процесса.
func owningApp(_ p: AudioProcess) -> (bundle: String, name: String, isApp: Bool) {
    var buf = [CChar](repeating: 0, count: 4096)
    if proc_pidpath(p.pid, &buf, UInt32(buf.count)) > 0 {
        let path = String(cString: buf)
        if let r = path.range(of: ".app/") {
            let appPath = String(path[..<r.lowerBound]) + ".app"
            if let b = Bundle(path: appPath), let id = b.bundleIdentifier {
                let name = (b.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String)
                    ?? (b.object(forInfoDictionaryKey: "CFBundleName") as? String)
                    ?? URL(fileURLWithPath: appPath).deletingPathExtension().lastPathComponent
                return (id, name, true)
            }
        }
    }
    let name = NSRunningApplication(processIdentifier: p.pid)?.localizedName ?? p.bundle
    return (p.bundle, name, false)
}

func listApps() {
    var apps: [String: [String: Any]] = [:]
    var order: [String] = []
    let me = getpid()
    for p in audioProcesses() where p.pid > 0 && p.pid != me {
        let app = owningApp(p)
        if app.bundle.isEmpty || !app.isApp { continue }
        if apps[app.bundle] == nil {
            apps[app.bundle] = ["bundle": app.bundle, "name": app.name, "playing": false, "pids": [Int]()]
            order.append(app.bundle)
        }
        apps[app.bundle]!["pids"] = (apps[app.bundle]!["pids"] as! [Int]) + [Int(p.pid)]
        if p.playing { apps[app.bundle]!["playing"] = true }
    }
    let rows = order.compactMap { apps[$0] }.sorted {
        let a = ($0["playing"] as! Bool) ? 0 : 1, b = ($1["playing"] as! Bool) ? 0 : 1
        if a != b { return a < b }
        return ($0["name"] as! String).localizedCaseInsensitiveCompare($1["name"] as! String) == .orderedAscending
    }
    let data = (try? JSONSerialization.data(withJSONObject: rows, options: [.sortedKeys])) ?? Data("[]".utf8)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([0x0A]))
}

// MARK: - Буфер между потоком Core Audio и выдачей по часам

final class SampleFifo {
    private var buf: [Float] = []
    private let lock = NSLock()
    private let limit = Int(outRate * 2)          // больше 2 с — отбрасываем старое (защита от дрейфа часов)

    func push(_ p: UnsafePointer<Float>, _ n: Int) {
        lock.lock(); defer { lock.unlock() }
        buf.append(contentsOf: UnsafeBufferPointer(start: p, count: n))
        if buf.count > limit { buf.removeFirst(buf.count - limit) }
    }

    /// Ровно n отсчётов: что пришло, остальное — нули. Второе значение — сколько пришло на самом деле.
    func pop(_ n: Int) -> ([Float], Int) {
        lock.lock(); defer { lock.unlock() }
        let k = min(n, buf.count)
        var out = Array(buf.prefix(k))
        buf.removeFirst(k)
        if k < n { out.append(contentsOf: repeatElement(0, count: n - k)) }
        return (out, k)
    }

    func clear() { lock.lock(); buf.removeAll(); lock.unlock() }
}

// MARK: - Tap

struct TapError: Error, CustomStringConvertible {
    let step: String
    let status: OSStatus
    var description: String { "\(step): OSStatus \(status)" }
}

func check(_ status: OSStatus, _ step: String) throws {
    if status != noErr { throw TapError(step: step, status: status) }
}

final class ProcessTap {
    private(set) var tapID = AudioObjectID(kAudioObjectUnknown)
    private(set) var aggregateID = AudioObjectID(kAudioObjectUnknown)
    private var ioProc: AudioDeviceIOProcID?

    /// include — процессы для записи; если nil, пишем всё, кроме exclude.
    init(include: [AudioObjectID]?, exclude: [AudioObjectID], output: AudioObjectID, fifo: SampleFifo) throws {
        let desc = include.map { CATapDescription(stereoMixdownOfProcesses: $0) }
            ?? CATapDescription(stereoGlobalTapButExcludeProcesses: exclude)
        desc.uuid = UUID()
        desc.isPrivate = true
        desc.muteBehavior = .unmuted          // пользователь продолжает слышать звонок
        try check(AudioHardwareCreateProcessTap(desc, &tapID), "AudioHardwareCreateProcessTap")

        let outputUID = readString(output, kAudioDevicePropertyDeviceUID) ?? ""
        let config: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Dewiar meeting tap",
            kAudioAggregateDeviceUIDKey: "pro.dewiar.tap." + UUID().uuidString,
            kAudioAggregateDeviceMainSubDeviceKey: outputUID,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outputUID]],
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapUIDKey: desc.uuid.uuidString,
                                               kAudioSubTapDriftCompensationKey: true]],
        ]
        do {
            try check(AudioHardwareCreateAggregateDevice(config as CFDictionary, &aggregateID),
                      "AudioHardwareCreateAggregateDevice")
            guard var asbd = readProp(tapID, kAudioTapPropertyFormat, AudioStreamBasicDescription()),
                  let inFormat = AVAudioFormat(streamDescription: &asbd),
                  let outFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: outRate,
                                                channels: 1, interleaved: false),
                  let converter = AVAudioConverter(from: inFormat, to: outFormat)
            else { throw TapError(step: "формат tap", status: -1) }
            converter.downmix = true
            report("tap", ["rate": inFormat.sampleRate, "channels": Int(inFormat.channelCount)])

            let queue = DispatchQueue(label: "pro.dewiar.tap.io", qos: .userInteractive)
            try check(AudioDeviceCreateIOProcIDWithBlock(&ioProc, aggregateID, queue) { _, input, _, _, _ in
                guard let inBuf = AVAudioPCMBuffer(pcmFormat: inFormat, bufferListNoCopy: input, deallocator: nil),
                      inBuf.frameLength > 0 else { return }
                let capacity = AVAudioFrameCount(Double(inBuf.frameLength) * outRate / inFormat.sampleRate) + 64
                guard let outBuf = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: capacity) else { return }
                var given = false
                var error: NSError?
                converter.convert(to: outBuf, error: &error) { _, status in
                    if given { status.pointee = .noDataNow; return nil }
                    given = true
                    status.pointee = .haveData
                    return inBuf
                }
                if outBuf.frameLength > 0, let ch = outBuf.floatChannelData {
                    fifo.push(ch[0], Int(outBuf.frameLength))
                }
            }, "AudioDeviceCreateIOProcIDWithBlock")
            try check(AudioDeviceStart(aggregateID, ioProc), "AudioDeviceStart")
        } catch {
            close()
            throw error
        }
    }

    func close() {
        if let ioProc {
            AudioDeviceStop(aggregateID, ioProc)
            AudioDeviceDestroyIOProcID(aggregateID, ioProc)
        }
        if aggregateID != kAudioObjectUnknown { AudioHardwareDestroyAggregateDevice(aggregateID) }
        if tapID != kAudioObjectUnknown { AudioHardwareDestroyProcessTap(tapID) }
        ioProc = nil
        aggregateID = AudioObjectID(kAudioObjectUnknown)
        tapID = AudioObjectID(kAudioObjectUnknown)
    }
}

// MARK: - Захват

final class Recorder {
    let targets: Set<String>?          // nil — весь звук системы
    let excludePIDs: [pid_t]
    let fifo = SampleFifo()
    var tap: ProcessTap?
    var signature = ""
    var lastStatus = ""
    let started = ProcessInfo.processInfo.systemUptime
    var written = 0
    var zeroSince: Date?
    var zerosReported = false
    let stdout = FileHandle.standardOutput

    init(target: String, excludePIDs: [pid_t]) {
        targets = target == "all" ? nil : Set(target.split(separator: ",").map { String($0) })
        self.excludePIDs = excludePIDs + [getpid()]
    }

    func status(_ text: String, _ extra: [String: Any] = [:]) {
        guard text != lastStatus else { return }
        lastStatus = text
        var f = extra
        f["text"] = text
        report("status", f)
    }

    /// Раз в секунду: пересоздать tap, если сменились играющие процессы или устройство вывода
    /// (подключили AirPods, выбрали другие колонки, звонок открыл новый процесс).
    func refresh() {
        let output = defaultOutput()
        let procs = audioProcesses().filter { $0.pid > 0 && $0.pid != getpid() }
        let include: [AudioObjectID]?
        var sig: String
        if let targets {
            let chosen = procs.filter { $0.playing && targets.contains(owningApp($0).bundle) }
            include = chosen.map { $0.object }
            sig = "app:" + chosen.map { String($0.object) }.sorted().joined(separator: ",")
        } else {
            let excluded = excludePIDs.map { processObject(for: $0) }.filter { $0 != kAudioObjectUnknown }
            include = nil
            sig = "all-but:" + excluded.map { String($0) }.sorted().joined(separator: ",")
        }
        sig += "@\(output)"
        guard sig != signature else { return }
        signature = sig
        tap?.close()
        tap = nil
        fifo.clear()
        if let include, include.isEmpty {
            status("приложение звонка пока не воспроизводит звук — жду", ["state": "waiting"])
            return
        }
        let exclude = excludePIDs.map { processObject(for: $0) }.filter { $0 != kAudioObjectUnknown }
        do {
            tap = try ProcessTap(include: include, exclude: exclude, output: output, fifo: fifo)
            status(targets == nil ? "записываю звук системы (кроме Dewiar)" : "записываю звук приложения",
                   ["state": "capturing", "output": readString(output, kAudioObjectPropertyName) ?? ""])
        } catch {
            signature = ""                 // повторить на следующем шаге
            status("tap не создан: \(error)", ["state": "error"])
        }
    }

    /// Выдать столько отсчётов, сколько положено по часам; тишину — нулями.
    func pump() {
        let due = Int((ProcessInfo.processInfo.systemUptime - started) * outRate) - written
        guard due > 0 else { return }
        let (samples, real) = fifo.pop(due)
        written += due
        trackZeros(samples, real: real)
        var pcm = [Int16](repeating: 0, count: samples.count)
        for i in 0..<samples.count {
            pcm[i] = Int16(max(-1.0, min(1.0, samples[i])) * 32767.0)
        }
        let data = pcm.withUnsafeBufferPointer { Data(buffer: $0) }
        do {
            try stdout.write(contentsOf: data)
        } catch {
            tap?.close()               // читатель (Dewiar) закрыл канал — выходим
            exit(0)
        }
    }

    /// Tap есть, кто-то играет, а приходят только нули → почти наверняка нет разрешения
    /// «Запись системного звука» (macOS в этом случае не сообщает ошибку, а отдаёт тишину).
    func trackZeros(_ s: [Float], real: Int) {
        guard tap != nil else { zeroSince = nil; return }
        let someonePlaying = audioProcessesPlayingCached
        let silent = !s.prefix(real).contains { abs($0) > 1e-6 }
        if someonePlaying && silent {
            if zeroSince == nil { zeroSince = Date() }
            if !zerosReported, let z = zeroSince, Date().timeIntervalSince(z) > 6 {
                zerosReported = true
                report("warning", ["code": "zeros",
                                   "text": "звук идёт, но в запись приходят нули — разрешите Dewiar «Запись системного звука» в «Конфиденциальность и безопасность»"])
            }
        } else if !silent {
            zeroSince = nil
            zerosReported = false
        }
    }

    var audioProcessesPlayingCached = false

    func run() {
        signal(SIGPIPE, SIG_IGN)
        for s in [SIGINT, SIGTERM, SIGHUP] {
            signal(s, SIG_IGN)
            let src = DispatchSource.makeSignalSource(signal: s, queue: .main)
            src.setEventHandler { [self] in tap?.close(); exit(0) }
            src.resume()
            keepAlive.append(src as AnyObject)
        }
        refresh()
        let scan = DispatchSource.makeTimerSource(queue: .main)
        scan.schedule(deadline: .now() + 1, repeating: 1)
        scan.setEventHandler { [self] in
            refresh()
            let me = getpid()
            audioProcessesPlayingCached = audioProcesses().contains {
                $0.playing && $0.pid != me && !excludePIDs.contains($0.pid)
                    && (targets == nil || targets!.contains(owningApp($0).bundle))
            }
        }
        scan.resume()
        let pacer = DispatchSource.makeTimerSource(queue: .main)
        pacer.schedule(deadline: .now() + 0.05, repeating: 0.05)
        pacer.setEventHandler { [self] in pump() }
        pacer.resume()
        keepAlive.append(scan as AnyObject)
        keepAlive.append(pacer as AnyObject)
        report("ready", ["rate": Int(outRate), "format": "s16le", "channels": 1])
        dispatchMain()
    }
}

// MARK: - main

let args = Array(CommandLine.arguments.dropFirst())
if args.first == "--self-test" {
    // Проверить обработку AudioBufferList без устройств и без запроса доступа TCC.
    let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48000, channels: 2, interleaved: false)!
    let source = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 4800)!
    source.frameLength = 4800
    for channel in 0..<2 {
        for n in 0..<4800 {
            source.floatChannelData![channel][n] = Float(sin(Double(n) * 2 * .pi * 440 / 48000)) * 0.25
        }
    }
    let wrapped = AVAudioPCMBuffer(pcmFormat: format, bufferListNoCopy: source.audioBufferList, deallocator: nil)!
    let outputFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: outRate, channels: 1, interleaved: false)!
    let converter = AVAudioConverter(from: format, to: outputFormat)!
    converter.downmix = true
    let converted = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: 1700)!
    var given = false
    var error: NSError?
    converter.convert(to: converted, error: &error) { _, status in
        if given { status.pointee = .noDataNow; return nil }
        given = true; status.pointee = .haveData; return wrapped
    }
    let peak = (0..<Int(converted.frameLength)).map { abs(converted.floatChannelData![0][$0]) }.max() ?? 0
    // AVAudioConverter keeps a small priming tail until the next input arrives.
    guard wrapped.frameLength == 4800, converted.frameLength > 1000, peak > 0.1, error == nil else {
        report("error", ["code": "self_test", "frames": Int(wrapped.frameLength), "converted": Int(converted.frameLength)])
        exit(1)
    }
    print("OK: stereo AudioBufferList → mono 16 kHz, nonzero signal")
    exit(0)
}
if args.first == "--list" {
    listApps()
    exit(0)
}
guard args.count >= 2, args[0] == "--capture" else {
    report("error", ["code": "usage", "text": "dewiar-audio-tap --list | --capture all|<bundle-id>[,…] [--exclude-pid N]"])
    exit(2)
}
var exclude: [pid_t] = []
var i = 2
while i < args.count {
    if args[i] == "--exclude-pid", i + 1 < args.count, let p = Int32(args[i + 1]) {
        exclude.append(p)
        i += 2
    } else {
        i += 1
    }
}
Recorder(target: args[1], excludePIDs: exclude).run()
