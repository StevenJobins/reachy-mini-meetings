// screencap: what the headset sees of this Mac when sharing, through ScreenCaptureKit (as Zoom does): a whole screen,
// one window (also while other windows lie on it), or all windows of one app.
//
//   screencap list                                   # JSON: the screens and the windows that can be shared
//   screencap display <id> | window <id> | app <pid> [--width 1280] [--fps 30] [--thumb path.jpg]
//
// Capturing writes one line "nv12 <width> <height> <fps>" to stdout, then raw NV12 frames of that size, fps times a
// second (the last picture again while nothing changes on the screen), until stdout closes or the source is gone.
// robot/scripts/share_screen.py reads them; --thumb writes a small JPEG of the picture every second for the app's
// window. Errors go to stderr, one line, and the exit code is not 0.
//
// Built by build_app.sh into the app (Contents/MacOS/screencap), so macOS asks about Screen Recording for the app.
// macOS 14 or newer (scalesToFit).

import AppKit
import CoreImage
import Foundation
import ScreenCaptureKit

setvbuf(stdout, nil, _IONBF, 0)
signal(SIGPIPE, SIG_IGN)
_ = NSApplication.shared   // for the names of the screens
var keep: [AnyObject] = []   // what must live while capturing

func fail(_ text: String, _ code: Int32 = 1) -> Never {
    FileHandle.standardError.write((text + "\n").data(using: .utf8)!)
    exit(code)
}

func shareable() async -> SCShareableContent {
    do {
        return try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
    } catch {
        fail("no picture of the screen: allow Screen Recording for Reachy Meetings in System Settings > Privacy & Security", 2)
    }
}

func displayName(_ id: CGDirectDisplayID) -> String {
    for screen in NSScreen.screens {
        if let n = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber, n.uint32Value == id {
            return screen.localizedName
        }
    }
    return "Screen"
}

/// Windows a person would pick: normal level, a title or at least a real size, not ours.
func pickable(_ w: SCWindow) -> Bool {
    guard w.windowLayer == 0, w.isOnScreen, let app = w.owningApplication else { return false }
    if app.processID == getppid() || app.bundleIdentifier == "ch.ethz.reachymeetings.laptop" { return false }
    return w.frame.width >= 120 && w.frame.height >= 80
}

func list() async {
    let content = await shareable()
    let main = CGMainDisplayID()
    let displays = content.displays.sorted { ($0.displayID == main ? 0 : 1, $0.displayID) < ($1.displayID == main ? 0 : 1, $1.displayID) }
        .map { ["id": Int($0.displayID), "name": displayName($0.displayID), "width": $0.width, "height": $0.height] as [String: Any] }
    let windows = content.windows.filter(pickable).map { w -> [String: Any] in
        let app = w.owningApplication!
        return ["id": Int(w.windowID), "title": w.title ?? "", "app": app.applicationName, "bundle": app.bundleIdentifier,
                "pid": Int(app.processID), "width": Int(w.frame.width), "height": Int(w.frame.height)]
    }
    let data = try! JSONSerialization.data(withJSONObject: ["displays": displays, "windows": windows], options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
    exit(0)
}

final class Capture: NSObject, SCStreamOutput, SCStreamDelegate {
    let width: Int, height: Int, fps: Int, thumb: String?
    let lock = NSLock()
    var frame: Data
    var latest: CVPixelBuffer?
    let ci = CIContext()

    init(width: Int, height: Int, fps: Int, thumb: String?) {
        self.width = width; self.height = height; self.fps = fps; self.thumb = thumb
        frame = Data(count: width * height * 3 / 2)
        // black until the first picture (Y 16, Cb Cr 128: video range)
        frame.withUnsafeMutableBytes { p in
            memset(p.baseAddress!, 16, width * height)
            memset(p.baseAddress! + width * height, 128, width * height / 2)
        }
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sample: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen, sample.isValid,
              let info = (CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]])?.first,
              let raw = info[.status] as? Int, SCFrameStatus(rawValue: raw) == .complete,
              let pb = CMSampleBufferGetImageBuffer(sample) else { return }
        CVPixelBufferLockBaseAddress(pb, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pb, .readOnly) }
        guard CVPixelBufferGetWidth(pb) == width, CVPixelBufferGetHeight(pb) == height,
              CVPixelBufferGetPlaneCount(pb) == 2 else { return }
        lock.lock()
        frame.withUnsafeMutableBytes { out in
            var o = out.baseAddress!
            for (plane, rows) in [(0, height), (1, height / 2)] {
                let base = CVPixelBufferGetBaseAddressOfPlane(pb, plane)!
                let stride = CVPixelBufferGetBytesPerRowOfPlane(pb, plane)
                for r in 0..<rows {
                    memcpy(o, base + r * stride, width)
                    o += width
                }
            }
        }
        latest = pb
        lock.unlock()
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fail("the shared window or screen is gone (\(error.localizedDescription))", 3)
    }

    /// The last picture, fps times a second: a steady stream for the encoder, also while nothing moves.
    func send() {
        lock.lock()
        let copy = frame
        lock.unlock()
        let ok = copy.withUnsafeBytes { p -> Bool in
            var done = 0
            while done < p.count {
                let n = write(1, p.baseAddress! + done, p.count - done)
                if n <= 0 { return false }
                done += n
            }
            return true
        }
        if !ok { exit(0) }   // the reader went away: sharing was stopped
    }

    func writeThumb() {
        guard let thumb else { return }
        lock.lock()
        let pb = latest
        lock.unlock()
        guard let pb else { return }
        let image = CIImage(cvPixelBuffer: pb)
        let scale = 480.0 / Double(width)
        let small = image.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        guard let space = CGColorSpace(name: CGColorSpace.sRGB),
              let data = ci.jpegRepresentation(of: small, colorSpace: space,
                                               options: [CIImageRepresentationOption(rawValue: kCGImageDestinationLossyCompressionQuality as String): 0.7])
        else { return }
        let tmp = thumb + ".tmp"
        if FileManager.default.createFile(atPath: tmp, contents: data) {
            _ = rename(tmp, thumb)
        }
    }
}

func even(_ x: Double) -> Int { max(2, Int((x / 2).rounded()) * 2) }

func capture(kind: String, id: Int, maxWidth: Int, fps: Int, thumb: String?) async {
    let content = await shareable()
    let filter: SCContentFilter
    var size: CGSize
    switch kind {
    case "display":
        guard let d = content.displays.first(where: { Int($0.displayID) == id }) else { fail("that screen is not there any more") }
        filter = SCContentFilter(display: d, excludingWindows: [])
        size = CGSize(width: d.width, height: d.height)
    case "window":
        guard let w = content.windows.first(where: { Int($0.windowID) == id }) else { fail("that window is not open any more") }
        filter = SCContentFilter(desktopIndependentWindow: w)
        size = w.frame.size
    case "app":
        guard let app = content.applications.first(where: { Int($0.processID) == id }) else { fail("that app is not running any more") }
        // the screen with most of its windows
        let mine = content.windows.filter { $0.owningApplication?.processID == app.processID && pickable($0) }
        let d = content.displays.max { a, b in
            mine.filter { a.frame.intersects($0.frame) }.count < mine.filter { b.frame.intersects($0.frame) }.count
        } ?? content.displays.first!
        filter = SCContentFilter(display: d, including: [app], exceptingWindows: [])
        size = CGSize(width: d.width, height: d.height)
    default:
        fail("usage: screencap list | display <id> | window <id> | app <pid> [--width 1280] [--fps 30] [--thumb path]")
    }
    // the picture keeps the shape of what is shared at the start; a window resized later is fitted in (black bars)
    let width = even(min(Double(maxWidth), Double(size.width) * 2))
    let height = even(Double(width) * Double(size.height) / Double(size.width))

    let config = SCStreamConfiguration()
    config.width = width
    config.height = height
    config.pixelFormat = kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange
    config.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(fps))
    config.queueDepth = 5
    config.showsCursor = true
    config.scalesToFit = true
    config.preservesAspectRatio = true
    config.capturesAudio = false

    let cap = Capture(width: width, height: height, fps: fps, thumb: thumb)
    let stream = SCStream(filter: filter, configuration: config, delegate: cap)
    do {
        try stream.addStreamOutput(cap, type: .screen, sampleHandlerQueue: DispatchQueue(label: "frames"))
        try await stream.startCapture()
    } catch {
        fail("could not start capturing: \(error.localizedDescription)", 2)
    }
    print("nv12 \(width) \(height) \(fps)")

    let out = DispatchSource.makeTimerSource(queue: DispatchQueue(label: "out"))
    out.schedule(deadline: .now(), repeating: .nanoseconds(1_000_000_000 / fps), leeway: .milliseconds(2))
    out.setEventHandler { cap.send() }
    out.resume()
    let side = DispatchSource.makeTimerSource(queue: DispatchQueue(label: "side"))
    side.schedule(deadline: .now() + 0.5, repeating: 1.0)
    side.setEventHandler { cap.writeThumb() }
    side.resume()
    keep = [stream, cap, out as AnyObject, side as AnyObject]
    if kind == "window" {   // a closed window does not always stop the stream: look every two seconds
        let watch = DispatchSource.makeTimerSource(queue: DispatchQueue(label: "watch"))
        watch.schedule(deadline: .now() + 2, repeating: 2.0)
        watch.setEventHandler {
            Task {
                let now = try? await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false)
                if let now, !now.windows.contains(where: { Int($0.windowID) == id }) { fail("the shared window was closed", 3) }
            }
        }
        watch.resume()
        keep.append(watch as AnyObject)
    }
}

let args = Array(CommandLine.arguments.dropFirst())
func option(_ name: String, _ fallback: String?) -> String? {
    if let i = args.firstIndex(of: name), i + 1 < args.count { return args[i + 1] }
    return fallback
}

Task {
    guard let kind = args.first else { fail("usage: screencap list | display <id> | window <id> | app <pid>") }
    if kind == "list" { await list() }
    guard args.count >= 2, let id = Int(args[1]) else { fail("usage: screencap \(kind) <id>") }
    await capture(kind: kind, id: id, maxWidth: Int(option("--width", "1280")!) ?? 1280,
                  fps: Int(option("--fps", "30")!) ?? 30, thumb: option("--thumb", nil))
}
dispatchMain()
