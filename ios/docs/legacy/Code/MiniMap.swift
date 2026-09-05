import SwiftUI

struct MiniMap: View {
    @ObservedObject var model: LocatorModel
    @State private var radius = 8.0
    private let mint = Color(red: 0.56, green: 0.96, blue: 0.76)
    var body: some View {
        VStack(spacing: 14) {
            HStack {
                Label("LOCAL MAP", systemImage: "scope").font(.system(size: 11, weight: .semibold, design: .monospaced))
                Spacer()
                Text("↑ PHONE FACING").font(.system(size: 10, design: .monospaced)).foregroundStyle(.secondary)
            }
            GeometryReader { geometry in
                let size = Double(min(geometry.size.width, geometry.size.height))
                let scale = (size / 2 - 18) / radius
                let center = CGPoint(x: size / 2, y: size / 2)
                Canvas { context, _ in
                    func projected(_ point: MapPoint) -> CGPoint {
                        let p = point.relative(to: model.phone, heading: model.heading)
                        return CGPoint(x: center.x + p.x * scale, y: center.y - p.y * scale)
                    }
                    for index in 0 ... 8 {
                        let offset = Double(index) * size / 8
                        var grid = Path()
                        grid.move(to: CGPoint(x: offset, y: 0)); grid.addLine(to: CGPoint(x: offset, y: size))
                        grid.move(to: CGPoint(x: 0, y: offset)); grid.addLine(to: CGPoint(x: size, y: offset))
                        context.stroke(grid, with: .color(.white.opacity(index == 4 ? 0.15 : 0.055)), lineWidth: 1)
                    }
                    for meters in [radius / 2, radius] {
                        let r = meters * scale
                        context.stroke(Path(ellipseIn: CGRect(x: center.x - r, y: center.y - r, width: r * 2, height: r * 2)),
                                       with: .color(.white.opacity(0.10)), style: StrokeStyle(lineWidth: 1, dash: [3, 6]))
                    }
                    if let distance = model.distance, model.position == nil {
                        let r = distance * scale
                        context.stroke(Path(ellipseIn: CGRect(x: center.x - r, y: center.y - r, width: r * 2, height: r * 2)),
                                       with: .color(mint.opacity(0.5)), style: StrokeStyle(lineWidth: 2, dash: [6, 5]))
                    }
                    if !model.trail.isEmpty {
                        var path = Path()
                        path.addLines(model.trail.map(projected))
                        context.stroke(path, with: .color(.white.opacity(0.25)), style: StrokeStyle(lineWidth: 2, lineCap: .round))
                    }
                    if let estimate = model.position {
                        let p = projected(estimate.point)
                        let uncertainty = estimate.uncertainty * scale
                        context.fill(Path(ellipseIn: CGRect(x: p.x - uncertainty, y: p.y - uncertainty,
                                                           width: uncertainty * 2, height: uncertainty * 2)), with: .color(mint.opacity(0.10)))
                        context.stroke(Path(ellipseIn: CGRect(x: p.x - uncertainty, y: p.y - uncertainty,
                                                             width: uncertainty * 2, height: uncertainty * 2)), with: .color(mint.opacity(0.25)))
                        context.fill(Path(ellipseIn: CGRect(x: p.x - 7, y: p.y - 7, width: 14, height: 14)), with: .color(mint))
                        context.draw(Text("ESTIMATE").font(.system(size: 9, weight: .bold, design: .monospaced)).foregroundColor(mint),
                                     at: CGPoint(x: p.x, y: p.y - 20))
                    }
                    var cone = Path()
                    cone.move(to: center)
                    cone.addLine(to: CGPoint(x: center.x - 23, y: center.y - 48))
                    cone.addLine(to: CGPoint(x: center.x + 23, y: center.y - 48))
                    cone.closeSubpath()
                    context.fill(cone, with: .color(.white.opacity(0.10)))
                    context.fill(Path(ellipseIn: CGRect(x: center.x - 6, y: center.y - 6, width: 12, height: 12)), with: .color(.white))
                    context.draw(Text("YOU").font(.system(size: 9, weight: .bold, design: .monospaced)).foregroundColor(.white),
                                 at: CGPoint(x: center.x, y: center.y + 20))
                }
                .clipShape(RoundedRectangle(cornerRadius: 16))
                .accessibilityLabel(mapDescription)
            }
            .aspectRatio(1, contentMode: .fit)
            HStack {
                Circle().fill(model.position == nil ? .gray : mint).frame(width: 6, height: 6)
                Text(model.position == nil ? "Distance only · direction unknown" : "Approximate position · shaded uncertainty")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
                Spacer(minLength: 0)
                Menu {
                    ForEach([4.0, 8.0, 16.0, 32.0], id: \.self) { value in
                        Button("\(Int(value)) m radius") { radius = value }
                    }
                } label: { Text("\(Int(radius)) m ⌄").font(.system(size: 11, design: .monospaced)).foregroundStyle(mint) }
            }
            if let distance = model.distance, distance > radius, model.position == nil {
                Text("Distance ring is outside the map. Increase the map radius.").font(.caption2).foregroundStyle(.orange)
            }
            if let point = model.position?.point,
               point.distance(to: model.phone) > radius {
                Text("Estimate is beyond the map radius. Zoom out to see it.").font(.caption2).foregroundStyle(.orange)
            }
        }
        .padding(18)
        .background(Color.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 24))
        .overlay(RoundedRectangle(cornerRadius: 24).strokeBorder(.white.opacity(0.08)))
    }
    private var mapDescription: String {
        if let estimate = model.position {
            let p = estimate.point.relative(to: model.phone, heading: model.heading)
            return "Estimated target \(abs(p.x).formatted(.number.precision(.fractionLength(1)))) meters \(p.x >= 0 ? "right" : "left"), \(abs(p.y).formatted(.number.precision(.fractionLength(1)))) meters \(p.y >= 0 ? "ahead" : "behind"). Position is uncertain."
        }
        return "Direction unknown. Collect measurements while walking an L-shaped path."
    }
}
