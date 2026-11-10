// PhotoCapture.swift — one fall-check photo (PRD §10.1 capture_photo, §25.2).
//
// The AVCaptureSession is created for the shot and torn down right after it: the camera is never on
// otherwise. Front wide-angle camera, one frame, JPEG with the longest side ≤ 1280 px at quality 0.7,
// multipart POST /media/photo (`file`, `incident_id`) with the device token → `photo_id`.
// All capture work runs on a private serial queue; the class is not actor-isolated.

@preconcurrency import AVFoundation
import UIKit

enum PhotoError: Error, LocalizedError {
    case denied, noCamera, configFailed, captureFailed(String), encodeFailed
    var errorDescription: String? {
        switch self {
        case .denied: "camera permission denied"
        case .noCamera: "no front camera"
        case .configFailed: "capture session configuration failed"
        case .captureFailed(let m): "capture failed: \(m)"
        case .encodeFailed: "JPEG encode failed"
        }
    }
}

final class PhotoCapture: NSObject, AVCapturePhotoCaptureDelegate, @unchecked Sendable {
    private let queue = DispatchQueue(label: "hk.ahbo.photo")
    private var continuation: CheckedContinuation<Data, any Error>?
    private var session: AVCaptureSession?
    private var shot = 0                       // guards the watchdog against a later shot

    static let maxSide: CGFloat = 1280
    static let quality: CGFloat = 0.7

    /// Take one front-camera photo and return the JPEG (≤ 1280 px, q 0.7).
    func capture() async throws -> Data {
        guard await AVCaptureDevice.requestAccess(for: .video) else { throw PhotoError.denied }
        let raw: Data = try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Data, any Error>) in
            queue.async { self.start(cont) }
        }
        return try Self.downscale(raw)
    }

    private func start(_ cont: CheckedContinuation<Data, any Error>) {
        guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .front) else {
            cont.resume(throwing: PhotoError.noCamera); return
        }
        let s = AVCaptureSession()
        let output = AVCapturePhotoOutput()
        s.beginConfiguration()
        s.sessionPreset = .photo
        guard let input = try? AVCaptureDeviceInput(device: device), s.canAddInput(input), s.canAddOutput(output) else {
            s.commitConfiguration()
            cont.resume(throwing: PhotoError.configFailed); return
        }
        s.addInput(input)
        s.addOutput(output)
        s.commitConfiguration()
        s.startRunning()                       // blocking; we're on the private queue
        session = s
        continuation = cont
        shot += 1
        let thisShot = shot
        // Give auto-exposure a moment so the frame isn't black.
        queue.asyncAfter(deadline: .now() + 0.6) {
            let settings = AVCapturePhotoSettings(format: [AVVideoCodecKey: AVVideoCodecType.jpeg])
            output.capturePhoto(with: settings, delegate: self)
        }
        // Watchdog: never leave the camera on.
        queue.asyncAfter(deadline: .now() + 8) {
            if self.shot == thisShot { self.complete(.failure(PhotoError.captureFailed("timeout"))) }
        }
    }

    func photoOutput(_ output: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto, error: (any Error)?) {
        let result: Result<Data, any Error>
        if let error { result = .failure(PhotoError.captureFailed(error.localizedDescription)) }
        else if let data = photo.fileDataRepresentation() { result = .success(data) }
        else { result = .failure(PhotoError.captureFailed("no data")) }
        queue.async { self.complete(result) }
    }

    private func complete(_ result: Result<Data, any Error>) {
        session?.stopRunning()
        session = nil
        guard let c = continuation else { return }
        continuation = nil
        c.resume(with: result)
    }

    static func downscale(_ jpeg: Data) throws -> Data {
        guard let img = UIImage(data: jpeg) else { throw PhotoError.encodeFailed }
        let longest = max(img.size.width, img.size.height)
        let scale = min(1, maxSide / max(longest, 1))
        let size = CGSize(width: (img.size.width * scale).rounded(), height: (img.size.height * scale).rounded())
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        let out = UIGraphicsImageRenderer(size: size, format: format).image { _ in img.draw(in: CGRect(origin: .zero, size: size)) }
        guard let data = out.jpegData(compressionQuality: quality) else { throw PhotoError.encodeFailed }
        return data
    }
}
