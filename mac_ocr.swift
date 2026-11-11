import Foundation
import Vision
import PDFKit
import AppKit

// Local-only OCR. Positions are normalized page coordinates (bottom-left origin).
guard CommandLine.arguments.count == 3,
      let document = PDFDocument(url: URL(fileURLWithPath: CommandLine.arguments[1])) else {
    fputs("无法打开 PDF\n", stderr); exit(1)
}
let indices = CommandLine.arguments[2].split(separator: ",").compactMap { Int($0) }
do {
    for index in indices {
        guard let page = document.page(at: index) else { throw NSError(domain: "PDF", code: 1) }
        let bounds = page.bounds(for: .mediaBox)
        let size = NSSize(width: min(2400, bounds.width * 2.5), height: min(3200, bounds.height * 2.5))
        let thumbnail = page.thumbnail(of: size, for: .mediaBox)
        var rect = NSRect(origin: .zero, size: thumbnail.size)
        guard let cg = thumbnail.cgImage(forProposedRect: &rect, context: nil, hints: nil) else { throw NSError(domain: "render", code: 2) }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["zh-Hans", "en-US"]
        request.usesLanguageCorrection = false
        try VNImageRequestHandler(cgImage: cg, options: [:]).perform([request])
        let lines: [[String: Any]] = (request.results ?? []).compactMap { observation in
            guard let text = observation.topCandidates(1).first else { return nil }
            let b = observation.boundingBox
            return ["text": text.string, "confidence": text.confidence, "bbox": [b.minX, b.minY, b.width, b.height]]
        }
        let output: [String: Any] = ["page": index + 1, "lines": lines]
        let data = try JSONSerialization.data(withJSONObject: output, options: [.sortedKeys])
        print(String(data: data, encoding: .utf8)!)
        fflush(stdout)
    }
} catch {
    fputs("OCR 失败: \(error)\n", stderr); exit(2)
}
